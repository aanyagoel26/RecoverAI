"""RecoverAI Autonomous Recovery Engine.

Detect -> retrieve order/session -> analyse -> score -> choose safest action (policy engine) ->
execute permitted non-financial actions or request approval -> monitor outcome -> update analytics.

Nothing in here can move money. Payment status only changes through apply_status(), which accepts evidence from
the PayPal API, a verified PayPal webhook, or the clearly labelled demo simulator.
"""
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (AgentRun, AIAnalysis, CheckoutSession, CommunicationLog, Customer, Merchant, MerchantSettings, Order,
                      Payment, PaymentEvent, PayPalInvoice, RecoveryAction, RecoveryCase)
from . import classifier, communication, llm, policy, rails, scoring
from .audit import audit, money, notify

ACTIVE_CASE = ("OPEN", "AWAITING_APPROVAL", "IN_PROGRESS")
SOURCE_LABEL = {"PAYPAL_API": "PayPal API response", "PAYPAL_WEBHOOK": "Verified PayPal webhook", "DEMO_SIMULATOR": "Demo simulator"}


def utcnow():
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def status_changed_at(db: Session, payment: Payment) -> datetime:
    """When the payment last changed status (updated_at is not reliable: analysis touches the row)."""
    t = db.scalar(select(PaymentEvent.created_at).where(PaymentEvent.payment_id == payment.id, PaymentEvent.event_type == "status.changed")
                  .order_by(PaymentEvent.created_at.desc()).limit(1))
    return aware(t or payment.created_at) or utcnow()


def new_reference() -> str:
    return f"TXN-{secrets.token_hex(3).upper()}"


def merchant_settings(db: Session, merchant_id: str) -> MerchantSettings:
    s = db.scalar(select(MerchantSettings).where(MerchantSettings.merchant_id == merchant_id))
    if not s:
        s = MerchantSettings(merchant_id=merchant_id)
        db.add(s)
        db.flush()
    return s


# ---------------------------------------------------------------- payment lifecycle
def apply_status(db: Session, payment: Payment, new_status: str, *, source: str, detail: dict | None = None,
                 issue: str | None = None, run_agent: bool = True) -> bool:
    """Single entry point for payment state changes. Returns True if state changed."""
    old = payment.status
    if old == new_status:
        db.add(PaymentEvent(merchant_id=payment.merchant_id, payment_id=payment.id, event_type="status.confirmed",
                            from_status=old, to_status=new_status, source=source, detail=detail))
        return False
    if not policy.can_transition(old, new_status):
        db.add(PaymentEvent(merchant_id=payment.merchant_id, payment_id=payment.id, event_type="status.rejected_transition",
                            from_status=old, to_status=new_status, source=source,
                            detail={"reason": f"Illegal transition {old} -> {new_status} ignored", **(detail or {})}))
        audit(db, payment.merchant_id, "system", "payment.transition_rejected",
              f"Ignored out-of-order update {old} → {new_status} for {payment.reference}", entity_type="payment", entity_id=payment.id)
        return False
    payment.status = new_status
    payment.status_verified_by = source
    if issue:
        payment.paypal_issue = issue
    db.add(PaymentEvent(merchant_id=payment.merchant_id, payment_id=payment.id, event_type="status.changed",
                        from_status=old, to_status=new_status, source=source, detail=detail))
    audit(db, payment.merchant_id, "paypal" if source.startswith("PAYPAL") else "system", "payment.status",
          f"{payment.reference}: {old} → {new_status} (source: {source})", entity_type="payment", entity_id=payment.id)
    db.flush()

    if new_status == "COMPLETED":
        payment.completed_at = utcnow()
        mark_order_paid(db, payment)
    elif new_status in ("DECLINED", "FAILED") and run_agent:
        case = ensure_case(db, payment=payment)
        if case:
            analyse_case(db, case, payment=payment)
            plan_actions(db, case, trigger=f"{SOURCE_LABEL.get(source, source)}: {payment.reference} {old} → {new_status}"
                         + (f" ({issue})" if issue else ""))
    elif new_status == "PENDING":
        cancel_customer_actions(db, payment.order_id, "Payment pending at PayPal — reminders paused")
    elif new_status == "REFUNDED":
        order = db.get(Order, payment.order_id)
        order.status = "REFUNDED"
        cancel_customer_actions(db, payment.order_id, "Order refunded")
    return True


def mark_order_paid(db: Session, payment: Payment):
    order = db.get(Order, payment.order_id)
    already_paid = order.status == "PAID"
    order.status, order.paid_at = "PAID", order.paid_at or utcnow()
    for s in db.scalars(select(CheckoutSession).where(CheckoutSession.order_id == order.id)):
        s.status, s.paypal_order_captured = "CONVERTED", True
    cancel_customer_actions(db, order.id, "Payment completed — no further reminders")
    for case in db.scalars(select(RecoveryCase).where(RecoveryCase.order_id == order.id, RecoveryCase.status.in_(ACTIVE_CASE))):
        if case.payment_id == payment.id:
            case.status, case.closed_at = "CLOSED_ALREADY_PAID", utcnow()
            continue
        case.status = "RECOVERED"
        case.recovered_payment_id, case.recovered_amount_minor = payment.id, payment.amount_minor
        case.recovered_at = case.closed_at = utcnow()
        payment.is_recovery_attempt, payment.recovery_case_id = True, case.id
        cust = db.get(Customer, case.customer_id)
        audit(db, case.merchant_id, "agent", "case.recovered",
              f"Recovered {money(payment.amount_minor, payment.currency)} from {cust.name} ({order.order_number}) — verified by {payment.status_verified_by}",
              entity_type="recovery_case", entity_id=case.id, detail={"payment_id": payment.id, "data_source": payment.data_source})
        notify(db, case.merchant_id, "success", f"Recovered {money(payment.amount_minor, payment.currency)}",
               f"{cust.name} completed payment for {order.order_number}. Reminders stopped.", link=f"/recovery?case={case.id}", category="recovered")
    if already_paid and payment.status == "COMPLETED":
        others = db.scalar(select(func.count()).select_from(Payment).where(
            Payment.order_id == order.id, Payment.status == "COMPLETED", Payment.id != payment.id)) or 0
        if others:
            notify(db, payment.merchant_id, "danger", "Possible duplicate payment",
                   f"Order {order.order_number} has {others + 1} completed payments. Review for refund.", link=f"/payments/{payment.id}", category="duplicate")
            audit(db, payment.merchant_id, "system", "duplicate.completed_payment",
                  f"Order {order.order_number} has more than one completed payment", entity_type="order", entity_id=order.id)


def cancel_customer_actions(db: Session, order_id: str, reason: str):
    case_ids = select(RecoveryCase.id).where(RecoveryCase.order_id == order_id)
    for a in db.scalars(select(RecoveryAction).where(RecoveryAction.case_id.in_(case_ids),
                                                     RecoveryAction.status.in_(["PROPOSED", "SCHEDULED", "APPROVED"]))):
        if a.action_type in policy.CUSTOMER_FACING:
            a.status, a.blocked_reason = "CANCELLED", reason


# ---------------------------------------------------------------- cases
def ensure_case(db: Session, *, payment: Payment | None = None, session: CheckoutSession | None = None,
                case_type: str | None = None) -> RecoveryCase | None:
    order_id = payment.order_id if payment else session.order_id
    if policy.order_is_paid(db, order_id):
        return None
    existing = db.scalar(select(RecoveryCase).where(RecoveryCase.order_id == order_id, RecoveryCase.status.in_(ACTIVE_CASE)))
    if existing:  # one active case per order — dedupes repeated failures/webhooks
        if payment and existing.payment_id != payment.id:
            existing.payment_id = payment.id
        return existing
    order = db.get(Order, order_id)
    if payment:
        ctype = case_type or ("PENDING_PAYMENT" if payment.status == "PENDING" else
                              "APPROVAL_INCOMPLETE" if payment.status in ("CREATED", "APPROVED") else "FAILED_PAYMENT")
    else:
        ctype = "ABANDONED_CHECKOUT"
    started = (status_changed_at(db, payment) if payment else aware(session.abandoned_at or session.last_activity_at)) or utcnow()
    case = RecoveryCase(merchant_id=order.merchant_id, order_id=order.id, customer_id=order.customer_id,
                        payment_id=payment.id if payment else None, checkout_session_id=session.id if session else None,
                        case_type=ctype, amount_minor=order.amount_minor, currency=order.currency,
                        data_source=(payment.data_source if payment else session.data_source), created_at=min(started, utcnow()))
    db.add(case)
    db.flush()
    audit(db, order.merchant_id, "agent", "case.opened", f"Opened {ctype.replace('_', ' ').lower()} case for {order.order_number} ({money(order.amount_minor, order.currency)})",
          entity_type="recovery_case", entity_id=case.id)
    return case


def analyse_case(db: Session, case: RecoveryCase, payment: Payment | None = None) -> AIAnalysis:
    settings = merchant_settings(db, case.merchant_id)
    payment = payment or (db.get(Payment, case.payment_id) if case.payment_id else None)
    customer = db.get(Customer, case.customer_id)
    order = db.get(Order, case.order_id)
    if payment and payment.status == "PENDING":
        return _analyse_pending(db, case, payment, settings)
    if payment:
        cls = classifier.classify(payment.status, payment.paypal_issue, payment.raw_response)
    else:
        cls = classifier.classify("CREATED", None, None, has_session_only=True)
    attempts = db.scalar(select(func.count()).select_from(Payment).where(Payment.order_id == case.order_id)) or 0
    failed_at = status_changed_at(db, payment) if payment else (aware(case.created_at) or utcnow())
    prob, factors = scoring.recovery_probability(
        db, case.merchant_id, category=cls.category, amount_minor=case.amount_minor, customer_id=case.customer_id,
        failed_at=failed_at, attempts_for_order=attempts, has_consent=customer.recovery_contact_consent)
    rec = policy.recommend(db, category=cls.category, order_id=case.order_id, has_consent=customer.recovery_contact_consent,
                           mode=settings.recovery_mode, amount_str=money(case.amount_minor, case.currency), payment=payment)
    rail = None
    if rec.action in policy.CUSTOMER_FACING:
        rail = rails.choose_rail(category=cls.category, amount_minor=case.amount_minor, country=customer.country,
                                 attempts=attempts, case_type=case.case_type)
        if rail.key == "PAYPAL_INVOICE":
            rec.action = "SEND_PAYPAL_INVOICE"
            rec.reason = rec.reason + " " + rail.reason
    info = cls.info
    explanation, root_cause, generated_by = info["explain"], _root_cause(cls, payment), "rules"
    llm_model = None
    evidence = cls.evidence or ["No PayPal error details were recorded for this attempt."]
    try:
        out = llm.complete_json(
            "You are a payments analyst. Explain this payment problem to a merchant in plain English.\n"
            f"Category (decided by rules, do not change): {info['label']}\n"
            f"Payment status: {payment.status if payment else 'no payment (checkout session only)'}\n"
            f"Amount: {money(case.amount_minor, case.currency)}; order: {order.description}\n"
            f"Evidence from PayPal: {evidence}\n"
            f"Recommended action (decided by policy engine): {rec.action} — {rec.reason}\n"
            f"Recovery rail (PayPal product chosen by policy): {rail.label + ' — ' + rail.reason if rail else 'none'}\n"
            "Rules: use ONLY the evidence given. If evidence is thin, say so. Never claim to know the cardholder's "
            "bank balance or intent. Never promise recovery.\n"
            'Return JSON {"explanation": "2-3 sentences", "root_cause": "1-2 sentences"}',
            system="You are RecoverAI, a careful fintech analyst.", db=db, merchant_id=case.merchant_id, purpose="failure_analysis")
        if out.get("explanation"):
            explanation, root_cause, generated_by = str(out["explanation"])[:1200], str(out.get("root_cause") or root_cause)[:800], "llm"
            llm_model = out.get("_model")
    except llm.LLMUnavailable:
        pass

    analysis = AIAnalysis(
        merchant_id=case.merchant_id, payment_id=payment.id if payment else None, case_id=case.id,
        failure_category=cls.category, classification_basis=cls.basis, explanation=explanation, root_cause=root_cause,
        severity=info["severity"], recovery_probability=prob, probability_factors=factors,
        recommendation=rec.as_dict() | {"confidence": prob, "evidence": evidence, "rail": rail.as_dict() if rail else None},
        customer_action_required=info["customer"], merchant_action_required=info["merchant"] or rec.merchant_involved,
        generated_by=generated_by, model=llm_model)
    db.add(analysis)
    case.failure_category, case.recovery_probability = cls.category, prob
    hours = (utcnow() - aware(case.created_at)).total_seconds() / 3600
    case.priority_score, case.priority_reason = scoring.priority(case.amount_minor, prob, hours, cls.category,
                                                                 rec.requires_approval)
    if payment:
        payment.failure_category = cls.category if payment.status != "PENDING" else payment.failure_category
        payment.recovery_probability, payment.risk_level = prob, scoring.risk_level(prob)
        payment.recommended_action = rec.action
    audit(db, case.merchant_id, "agent", "analysis.completed",
          f"Analysed {order.order_number}: {info['label']}, {prob:.0%} recovery probability → {rec.action}",
          entity_type="recovery_case", entity_id=case.id, detail={"generated_by": generated_by})
    db.flush()
    return analysis


PENDING_REASONS = {  # PayPal capture status_details.reason values
    "PENDING_REVIEW": "PayPal is reviewing the payment (risk review).",
    "ECHECK": "The payer paid with an eCheck, which takes several business days to clear.",
    "UNILATERAL": "The receiving account is not yet confirmed/registered with PayPal.",
    "RECEIVING_PREFERENCE_MANDATES_MANUAL_ACTION": "The merchant account requires manual acceptance of this payment in PayPal.",
    "VERIFICATION_REQUIRED": "The merchant account must be verified before funds are released.",
    "BUYER_COMPLAINT": "The payer has opened a complaint.",
    "MULTI_CURRENCY": "The merchant account must accept or convert this currency in PayPal.",
}


def _analyse_pending(db: Session, case: RecoveryCase, payment: Payment, settings: MerchantSettings) -> AIAnalysis:
    reason = payment.paypal_issue
    hours = (utcnow() - status_changed_at(db, payment)).total_seconds() / 3600
    prob = 0.85 if hours < 72 else 0.7
    merchant_fix = reason in ("UNILATERAL", "RECEIVING_PREFERENCE_MANDATES_MANUAL_ACTION", "VERIFICATION_REQUIRED", "MULTI_CURRENCY")
    explanation = (f"PayPal accepted this payment but has not released it yet. {PENDING_REASONS.get(reason, 'PayPal did not give a specific reason.')} "
                   "This is not a failed payment: asking the customer to pay again could charge them twice.")
    rec = policy.Recommendation(
        "NOTIFY_MERCHANT" if merchant_fix else "WAIT_FOR_PENDING",
        "The pending reason requires action in your PayPal account." if merchant_fix else "Wait for PayPal's PAYMENT.CAPTURE.COMPLETED or DENIED webhook. No customer contact and no new charge.",
        "Payment completes without customer friction.", "Customer may be confused if contacted while pending.",
        False, False, True)
    factors = [{"factor": "Pending capture prior", "effect": 0.85, "detail": "Assumption: most pending PayPal captures complete"},
               {"factor": "Time pending", "effect": round(prob - 0.85, 2), "detail": f"{hours:.0f}h pending"}]
    a = AIAnalysis(merchant_id=case.merchant_id, payment_id=payment.id, case_id=case.id, failure_category="UNKNOWN",
                   classification_basis=f"Capture status PENDING (reason: {reason or 'none'}) — not a failure", explanation=explanation,
                   root_cause=PENDING_REASONS.get(reason, "PayPal has not completed the capture yet."), severity=25, recovery_probability=prob,
                   probability_factors=factors, recommendation=rec.as_dict() | {"confidence": prob, "evidence": [f"Capture status: PENDING, reason: {reason or 'not provided'}"]},
                   customer_action_required=False, merchant_action_required=merchant_fix, generated_by="rules")
    db.add(a)
    case.failure_category, case.recovery_probability = None, prob
    case.priority_score, case.priority_reason = scoring.priority(case.amount_minor, prob * 0.3, hours, "PENDING", False)
    case.priority_reason = "pending at PayPal — monitor only; " + case.priority_reason
    payment.recovery_probability, payment.risk_level, payment.recommended_action = prob, "LOW", rec.action
    db.flush()
    return a


def _root_cause(cls: classifier.Classification, payment: Payment | None) -> str:
    if cls.category == "UNKNOWN":
        return "PayPal did not return an issue code or decline reason for this attempt, so the cause cannot be determined from available data."
    if cls.category == "CHECKOUT_ABANDONED":
        return "The checkout session went inactive before any payment was approved."
    return f"{cls.basis}."


# ---------------------------------------------------------------- planning & execution
def latest_analysis(db: Session, case_id: str) -> AIAnalysis | None:
    return db.scalar(select(AIAnalysis).where(AIAnalysis.case_id == case_id).order_by(AIAnalysis.created_at.desc()).limit(1))


def plan_actions(db: Session, case: RecoveryCase, trigger: str = "Agent monitoring pass") -> list[RecoveryAction]:
    settings = merchant_settings(db, case.merchant_id)
    analysis = latest_analysis(db, case.id) or analyse_case(db, case)
    rec = analysis.recommendation
    action_type = rec["action"]
    created: list[RecoveryAction] = []

    if action_type == "CLOSE_CASE":
        case.status, case.closed_at = "CLOSED_ALREADY_PAID", utcnow()
        record_pipeline_run(db, case, trigger)
        return created

    def add(atype, idem, *, status, requires_approval, reason, scheduled_for=None, payload=None):
        if db.scalar(select(RecoveryAction.id).where(RecoveryAction.case_id == case.id, RecoveryAction.idempotency_key == idem)):
            return None  # idempotent: this step was already planned
        a = RecoveryAction(merchant_id=case.merchant_id, case_id=case.id, action_type=atype, status=status,
                           requires_approval=requires_approval, idempotency_key=idem, reason=reason,
                           scheduled_for=scheduled_for, payload=payload or {})
        db.add(a)
        db.flush()
        created.append(a)
        return a

    if action_type in policy.CUSTOMER_FACING:
        base = aware(case.created_at)
        for i, minutes in enumerate(settings.reminder_intervals_minutes[: settings.max_reminders_per_case]):
            due = base + timedelta(minutes=minutes)
            first = i == 0
            if settings.recovery_mode == "AUTOMATIC":
                status, needs = "SCHEDULED", False
            elif first:
                status, needs = "PROPOSED", True  # approval/manual: merchant decides on the first message now
            else:
                status, needs = "SCHEDULED", True
            atype = action_type if first or action_type != "SEND_PAYPAL_INVOICE" else "SEND_REMINDER"
            add(atype, f"{atype}:{i + 1}", status=status, requires_approval=needs,
                reason=rec["reason"] if first else f"Follow-up reminder #{i + 1} ({minutes} min after the case opened)",
                scheduled_for=None if (first and needs) else due, payload={"sequence": i + 1, "tone": settings.default_tone})
        case.status = "AWAITING_APPROVAL" if settings.recovery_mode != "AUTOMATIC" else "IN_PROGRESS"
    else:
        needs = bool(rec.get("requires_approval"))
        a = add(action_type, f"{action_type}:1", status="PROPOSED", requires_approval=needs, reason=rec["reason"])
        if a and settings.recovery_mode != "MANUAL" and not needs:
            execute_action(db, a, actor_type="agent", actor_id=None)
        case.status = "AWAITING_APPROVAL" if needs else "IN_PROGRESS"
    if created:
        audit(db, case.merchant_id, "agent", "actions.planned",
              f"Planned {len(created)} action(s): {', '.join(sorted({c.action_type for c in created}))} ({settings.recovery_mode} mode)",
              entity_type="recovery_case", entity_id=case.id)
    record_pipeline_run(db, case, trigger)
    return created


def _rail_of(analysis: AIAnalysis | None) -> dict | None:
    return (analysis.recommendation or {}).get("rail") if analysis else None


def record_pipeline_run(db: Session, case: RecoveryCase, trigger: str) -> AgentRun:
    """Persist the full detect→…→analytics trace for this decision, including live guardrail results."""
    settings = merchant_settings(db, case.merchant_id)
    analysis = latest_analysis(db, case.id)
    order = db.get(Order, case.order_id)
    payment = db.get(Payment, case.payment_id) if case.payment_id else None
    sess = db.get(CheckoutSession, case.checkout_session_id) if case.checkout_session_id else None
    attempts = db.scalar(select(func.count()).select_from(Payment).where(Payment.order_id == case.order_id)) or 0
    rec = analysis.recommendation if analysis else {"action": "NONE", "reason": ""}
    rail = _rail_of(analysis)
    actions = db.scalars(select(RecoveryAction).where(RecoveryAction.case_id == case.id).order_by(RecoveryAction.created_at)).all()
    first = next((a for a in actions if a.status in ("PROPOSED", "EXECUTED", "BLOCKED")), actions[0] if actions else None)
    checks = policy.guardrails(db, case, settings, rec["action"])
    amount = money(case.amount_minor, case.currency)
    top = sorted((analysis.probability_factors if analysis else [])[1:], key=lambda f: -abs(f.get("effect", 0)))[:2]
    if case.status in ("CLOSED_ALREADY_PAID",):
        outcome = "CLOSED"
    elif first and first.status == "EXECUTED":
        outcome = "EXECUTED"
    elif first and first.status == "BLOCKED":
        outcome = "BLOCKED"
    elif rec["action"] == "WAIT_FOR_PENDING":
        outcome = "WAITING"
    elif first and first.status == "PROPOSED" and first.requires_approval:
        outcome = "AWAITING_APPROVAL"
    else:
        outcome = "RECOMMENDED" if settings.recovery_mode == "MANUAL" else "EXECUTED"
    scheduled = sum(1 for a in actions if a.status == "SCHEDULED")
    n_pass = sum(1 for c in checks if c["status"] == "pass")
    n_info = sum(1 for c in checks if c["status"] == "info")
    steps = [
        {"key": "detect", "label": "Detect event", "status": "ok", "detail": trigger},
        {"key": "retrieve", "label": "Retrieve order & session", "status": "ok",
         "detail": f"{order.order_number} · {amount} · {attempts} attempt(s)" + (f" · checkout {sess.status.lower()}" if sess else "")},
        {"key": "analyse", "label": "Analyse transaction", "status": "ok" if analysis else "skipped",
         "detail": (f"{classifier.CATEGORY_INFO.get(analysis.failure_category, {}).get('label', analysis.failure_category)} — {analysis.classification_basis}"
                    f" ({'LLM explanation' if analysis.generated_by == 'llm' else 'rules explanation'})") if analysis else "No analysis"},
        {"key": "score", "label": "Score recovery probability", "status": "ok",
         "detail": f"{(case.recovery_probability or 0):.0%}" + (" · " + ", ".join(f"{f['factor']} {f['effect']:+.0%}" for f in top) if top else "")},
        {"key": "decide", "label": "Choose safest action & PayPal rail", "status": "ok",
         "detail": f"{policy.ACTIONS.get(rec['action'], (0, rec['action']))[1]}" + (f" · rail: {rail['label']}" if rail else "")},
        {"key": "guardrails", "label": "Trust Firewall", "status": "fail" if any(c["status"] == "fail" for c in checks) else
         "hold" if any(c["status"] == "hold" for c in checks) else "ok",
         "detail": f"{n_pass}/{len(checks) - n_info} checks passed" + (f" · {n_info} informational" if n_info else "")
                   + (" · waiting for merchant approval" if any(c["status"] == "hold" for c in checks) else "")},
        {"key": "act", "label": "Execute or request approval", "status": {"EXECUTED": "ok", "AWAITING_APPROVAL": "hold", "BLOCKED": "fail"}.get(outcome, "info"),
         "detail": {"EXECUTED": "Safe action executed", "AWAITING_APPROVAL": "Prepared — one click to approve", "BLOCKED": first.blocked_reason if first else "Blocked",
                    "WAITING": "No action: waiting for PayPal", "RECOMMENDED": "Recommendation only (manual mode)", "CLOSED": "Already paid — case closed"}[outcome]},
        {"key": "monitor", "label": "Monitor outcome", "status": "ok",
         "detail": "Listening for PAYMENT.CAPTURE.COMPLETED / INVOICING.INVOICE.PAID" + (f" · {scheduled} follow-up(s) queued" if scheduled else "")},
        {"key": "analytics", "label": "Update analytics", "status": "ok",
         "detail": f"{amount} tracked as revenue at risk until PayPal confirms payment" if outcome != "CLOSED" else "No revenue at risk"},
    ]
    run = AgentRun(merchant_id=case.merchant_id, case_id=case.id, payment_id=payment.id if payment else None, trigger=trigger[:200],
                   steps=steps, guardrails=checks, rail=rail, outcome=outcome, data_source=case.data_source,
                   summary=f"{order.order_number} ({amount}): {policy.ACTIONS.get(rec['action'], (0, rec['action']))[1]} → {outcome.replace('_', ' ').lower()}")
    db.add(run)
    db.flush()
    return run


def ensure_recovery_link(db: Session, case: RecoveryCase, with_rail: bool = True) -> str:
    order = db.get(Order, case.order_id)
    sess = db.get(CheckoutSession, case.checkout_session_id) if case.checkout_session_id else None
    if not sess:
        sess = db.scalar(select(CheckoutSession).where(CheckoutSession.order_id == order.id).order_by(CheckoutSession.created_at.desc()).limit(1))
    if not sess:
        sess = CheckoutSession(merchant_id=order.merchant_id, order_id=order.id, customer_id=order.customer_id,
                               status="ABANDONED", data_source=order.data_source)
        db.add(sess)
        db.flush()
        case.checkout_session_id = sess.id
    link = session_link(sess)
    rail = _rail_of(latest_analysis(db, case.id)) if with_rail else None
    if rail and rail["key"] in ("PAY_LATER", "VENMO"):
        link += "?rail=" + ("paylater" if rail["key"] == "PAY_LATER" else "venmo")
    return link


def _with_param(url: str, key: str, value: str) -> str:
    return url + ("&" if "?" in url else "?") + f"{key}={value}"


def session_link(sess: CheckoutSession) -> str:
    if not sess.recovery_token or (aware(sess.recovery_token_expires_at) or utcnow()) < utcnow():
        sess.recovery_token = secrets.token_urlsafe(24)
        sess.recovery_token_expires_at = utcnow() + timedelta(days=7)
    return f"{get_settings().frontend_url.rstrip('/')}/pay/{sess.recovery_token}"


def execute_action(db: Session, action: RecoveryAction, *, actor_type: str, actor_id: str | None,
                   tone: str | None = None, subject: str | None = None, body: str | None = None) -> RecoveryAction:
    if action.status in ("EXECUTED", "CANCELLED", "REJECTED"):
        return action  # idempotent
    case = db.get(RecoveryCase, action.case_id)
    settings = merchant_settings(db, case.merchant_id)
    merchant = db.get(Merchant, case.merchant_id)
    order = db.get(Order, case.order_id)

    if policy.order_is_paid(db, case.order_id) and action.action_type != "CLOSE_CASE":
        action.status, action.blocked_reason = "CANCELLED", "Order already paid"
        if case.status in ACTIVE_CASE:
            case.status, case.closed_at = "CLOSED_ALREADY_PAID", utcnow()
        return action

    if action.action_type == "RETRY_CAPTURE":
        from . import retry
        checks, summary = retry.execute_retry(db, action, case, settings, actor_type)
        if action.status == "BLOCKED":
            _record_execution_run(db, case, action, checks, actor_type)
            pay = db.get(Payment, case.payment_id) if case.payment_id else None
            if pay and not policy.order_is_paid(db, case.order_id) and pay.status not in ("PENDING",):
                # The buyer must approve again: fall back to a customer-approved checkout (never a blind re-charge)
                db.add(RecoveryAction(merchant_id=case.merchant_id, case_id=case.id, action_type="REQUEST_CUSTOMER_ACTION", status="PROPOSED",
                                      requires_approval=settings.recovery_mode != "AUTOMATIC", idempotency_key=f"REQUEST_CUSTOMER_ACTION:after-retry-{action.id[:8]}",
                                      reason="Capture retry was blocked by the Trust Firewall, so the safe path is a fresh, buyer-approved PayPal checkout.",
                                      payload={"sequence": 1, "tone": settings.default_tone}))
                case.status = "AWAITING_APPROVAL"
                notify(db, case.merchant_id, "warning", "Retry blocked — buyer approval needed", action.blocked_reason or "", link=f"/recovery?case={case.id}", category="blocked")
            db.flush()
            return action
        action.status, action.executed_at, action.result = "EXECUTED", utcnow(), {"summary": summary}
        if actor_type == "user":
            action.approved_by = actor_id
        audit(db, case.merchant_id, actor_type, "action.executed", summary, actor_id=actor_id, entity_type="recovery_action", entity_id=action.id)
        _record_execution_run(db, case, action, checks, actor_type, summary)
        db.flush()
        return action

    if action.action_type in policy.CUSTOMER_FACING:
        checks = policy.guardrails(db, case, settings, action.action_type,
                                   approved=actor_type == "user" or not action.requires_approval)
        failed = [c for c in checks if c["status"] == "fail"]
        ok, why = policy.can_contact(db, case, settings)
        if failed or not ok:
            why = failed[0]["detail"] if failed else why
            if failed and failed[0]["key"] == "consent":
                why = "Customer has not consented to recovery messages"
            action.status, action.blocked_reason = "BLOCKED", why
            audit(db, case.merchant_id, "agent", "action.blocked", f"{action.action_type} blocked by Trust Firewall: {why}",
                  entity_type="recovery_action", entity_id=action.id)
            notify(db, case.merchant_id, "warning", "Action blocked by Trust Firewall", f"{policy.ACTIONS[action.action_type][1]}: {why}",
                   link=f"/recovery?case={case.id}", category="blocked")
            _record_execution_run(db, case, action, checks, actor_type)
            return action
        link = ensure_recovery_link(db, case)
        invoice_note = None
        if action.action_type == "SEND_PAYPAL_INVOICE":
            link, invoice_note = _send_invoice_or_fallback(db, case, settings, merchant, order, link)
        elif (action.payload or {}).get("sequence", 1) > 1:
            inv = db.scalar(select(PayPalInvoice).where(PayPalInvoice.case_id == case.id, PayPalInvoice.status != "CANCELLED").limit(1))
            if inv and inv.payer_view_url:
                link = inv.payer_view_url
        from ..models import uid as _uid
        email_log_id = _uid()
        tracked = _with_param(link, "c", email_log_id) if "/pay/" in link else link  # click tracking on our own links only
        msg = communication.generate(db, case, tone=tone or (action.payload or {}).get("tone") or settings.default_tone,
                                     link=tracked, brand=settings.brand_name or merchant.name, support_email=settings.support_email)
        if subject:
            msg["subject"] = subject[:300]
        if body:
            body = body.replace(link, tracked) if tracked != link and tracked not in body else body
            if tracked not in body:
                body = body.rstrip() + f"\n\nContinue securely: {tracked}"
            msg["body"] = body
        cust = db.get(Customer, case.customer_id)
        log = CommunicationLog(id=email_log_id, merchant_id=case.merchant_id, case_id=case.id, customer_id=cust.id, tone=msg["tone"],
                               subject=msg["subject"], body=msg["body"], recovery_link=tracked, generated_by=msg["generated_by"])
        db.add(log)
        db.flush()
        communication.deliver(db, log, cust.email)
        channels = settings.channels or ["email"]
        sms_log = None
        if "sms" in channels and cust.phone:
            sms_id = _uid()
            sms_link = _with_param(link, "c", sms_id) if "/pay/" in link else link
            sms_log = CommunicationLog(id=sms_id, merchant_id=case.merchant_id, case_id=case.id, customer_id=cust.id, channel="sms", tone=msg["tone"],
                                       subject="SMS", recovery_link=sms_link, generated_by="rules",
                                       body=communication.sms_text(first_name=cust.name.split()[0], brand=settings.brand_name or merchant.name,
                                                                   order_number=order.order_number, amount=money(case.amount_minor, case.currency), link=sms_link))
            db.add(sms_log)
            db.flush()
            communication.deliver_sms(db, sms_log, cust.phone)
        case.reminders_sent += 1
        case.last_contacted_at = utcnow()
        case.status = "IN_PROGRESS"
        action.result = {"communication_id": log.id, "delivery_status": log.delivery_status, "recovery_link": tracked}
        if sms_log:
            action.result["sms"] = {"communication_id": sms_log.id, "delivery_status": sms_log.delivery_status}
        if invoice_note:
            action.result["invoice"] = invoice_note
        verb = action.action_type.replace('_', ' ').lower()
        summary = (f"Sent {verb} to {cust.name} (accepted by email provider)" if log.delivery_status == "SENT"
                   else f"Prepared {verb} for {cust.name} — preview only (email provider not configured, nothing was sent)"
                   if log.delivery_status == "PREVIEW_ONLY" else f"Message to {cust.name}: {log.delivery_status.lower()}")
        if sms_log:
            summary += f"; SMS {sms_log.delivery_status.replace('_', ' ').lower()}"
    elif action.action_type == "GENERATE_CHECKOUT_LINK":
        link = ensure_recovery_link(db, case)
        action.result = {"recovery_link": link}
        summary = f"Generated secure checkout link for {order.order_number}"
    elif action.action_type in ("NOTIFY_MERCHANT", "MANUAL_INVESTIGATION", "RECOMMEND_SUPPORT"):
        notify(db, case.merchant_id, "warning", f"{order.order_number} needs your attention", action.reason or "", link=f"/recovery?case={case.id}")
        action.result = {"notified": True}
        summary = f"Flagged {order.order_number} for merchant review: {action.action_type.replace('_', ' ').lower()}"
    elif action.action_type == "WAIT_FOR_PENDING":
        action.result = {"waiting": True}
        summary = f"Waiting for PayPal to resolve pending payment on {order.order_number}; no new charge requested"
    elif action.action_type == "CLOSE_CASE":
        case.status, case.closed_at = "CLOSED_ALREADY_PAID", utcnow()
        summary = f"Closed case for {order.order_number}"
    else:
        action.status, action.blocked_reason = "BLOCKED", "Unknown action type"
        return action

    action.status, action.executed_at = "EXECUTED", utcnow()
    if actor_type == "user":
        action.approved_by = actor_id
    audit(db, case.merchant_id, actor_type, "action.executed", summary, actor_id=actor_id,
          entity_type="recovery_action", entity_id=action.id, detail=action.result)
    db.flush()
    if action.action_type in policy.CUSTOMER_FACING:
        _record_execution_run(db, case, action, checks, actor_type, summary)
    return action


def _record_execution_run(db, case, action, checks, actor_type, summary=None):
    order = db.get(Order, case.order_id)
    analysis = latest_analysis(db, case.id)
    blocked = action.status == "BLOCKED"
    steps = [
        {"key": "detect", "label": "Approval received" if actor_type == "user" else "Scheduled step due", "status": "ok",
         "detail": f"{policy.ACTIONS[action.action_type][1]} for {order.order_number}"},
        {"key": "guardrails", "label": "Trust Firewall re-check", "status": "fail" if blocked else "ok",
         "detail": action.blocked_reason if blocked else f"{sum(1 for c in checks if c['status'] == 'pass')}/{len(checks)} checks passed at execution time"},
        {"key": "act", "label": "Execute", "status": "fail" if blocked else "ok", "detail": "Not executed" if blocked else (summary or "Executed")},
        {"key": "monitor", "label": "Monitor outcome", "status": "ok" if not blocked else "skipped",
         "detail": "Waiting for the customer to complete payment in PayPal; revenue counts only after PayPal confirms" if not blocked else "—"},
    ]
    db.add(AgentRun(merchant_id=case.merchant_id, case_id=case.id, payment_id=case.payment_id,
                    trigger=("Merchant approved" if actor_type == "user" else "Agent schedule") + f": {action.action_type}",
                    steps=steps, guardrails=checks, rail=_rail_of(analysis), outcome="BLOCKED" if blocked else "EXECUTED",
                    data_source=case.data_source,
                    summary=f"{order.order_number}: {policy.ACTIONS[action.action_type][1]} → {'blocked' if blocked else 'executed'}"))
    db.flush()


def _send_invoice_or_fallback(db, case, settings, merchant, order, checkout_link) -> tuple[str, dict]:
    """Create + send a PayPal Invoicing v2 invoice. Falls back (honestly recorded) to the checkout link."""
    from . import providers
    from .paypal_client import PayPalError, PayPalNotConfigured, invoice_id_from, invoice_payer_url
    existing = db.scalar(select(PayPalInvoice).where(PayPalInvoice.case_id == case.id, PayPalInvoice.status.in_(["SENT", "DRAFT", "UNPAID"])))
    if existing and existing.payer_view_url:
        return existing.payer_view_url, {"paypal_invoice_id": existing.paypal_invoice_id, "status": existing.status, "reused": True}
    if not providers.paypal_config(db, case.merchant_id):
        return checkout_link, {"fallback": "PayPal not configured — invoice rail unavailable, sent secure checkout link instead"}
    cust = db.get(Customer, case.customer_id)
    given, _, surname = cust.name.partition(" ")
    number = f"RA-{order.order_number}-{secrets.token_hex(2).upper()}"
    pp = providers.paypal_for(db, case.merchant_id)
    try:
        res = pp.create_invoice(invoice_number=number, reference=order.order_number, currency=case.currency, amount_minor=case.amount_minor,
                                item_name=order.description, recipient_email=cust.email, recipient_given=given or cust.name,
                                recipient_surname=surname or "-", business_name=settings.brand_name or merchant.name,
                                note="Thanks for your order — you can pay securely with PayPal.", request_id=f"inv-create-{case.id}")
        inv_id = invoice_id_from(res)
        sent = pp.send_invoice(inv_id, request_id=f"inv-send-{case.id}")
        url = invoice_payer_url(sent)
        if not url:
            url = invoice_payer_url(pp.get_invoice(inv_id))
    except (PayPalError, PayPalNotConfigured) as e:
        audit(db, case.merchant_id, "paypal", "invoice.error", f"PayPal invoice for {order.order_number} failed: {e}", entity_type="recovery_case", entity_id=case.id)
        return checkout_link, {"fallback": f"PayPal invoice failed ({getattr(e, 'issue', None) or getattr(e, 'name', 'error')}); sent checkout link instead"}
    inv = PayPalInvoice(merchant_id=case.merchant_id, case_id=case.id, order_id=order.id, paypal_invoice_id=inv_id, invoice_number=number,
                        status="SENT", payer_view_url=url, amount_minor=case.amount_minor, currency=case.currency, raw={"send": sent})
    db.add(inv)
    audit(db, case.merchant_id, "paypal", "invoice.sent", f"PayPal invoice {inv_id} sent for {order.order_number} ({money(case.amount_minor, case.currency)})",
          entity_type="recovery_case", entity_id=case.id)
    db.flush()
    return url or checkout_link, {"paypal_invoice_id": inv_id, "status": "SENT", "payer_view_url": url}


# ---------------------------------------------------------------- monitoring loop
def tick(db: Session, merchant_id: str) -> dict:
    settings = merchant_settings(db, merchant_id)
    now = utcnow()
    summary = {"abandoned_detected": 0, "cases_opened": 0, "actions_executed": 0, "actions_awaiting_approval": 0,
               "pending_flagged": 0, "cases_closed_paid": 0}

    # 1. abandonment detection (independent of PayPal payment failures)
    cutoff = now - timedelta(minutes=settings.abandonment_threshold_minutes)
    for s in db.scalars(select(CheckoutSession).where(CheckoutSession.merchant_id == merchant_id, CheckoutSession.status == "ACTIVE")):
        in_flight = db.scalar(select(Payment.id).where(Payment.order_id == s.order_id, Payment.status.in_(["APPROVED", "PENDING"])).limit(1))
        if aware(s.last_activity_at) < cutoff and not policy.order_is_paid(db, s.order_id) and not in_flight:
            s.status, s.abandoned_at = "ABANDONED", now
            summary["abandoned_detected"] += 1
            audit(db, merchant_id, "agent", "checkout.abandoned", "Detected abandoned checkout session", entity_type="checkout_session", entity_id=s.id)
    for s in db.scalars(select(CheckoutSession).where(CheckoutSession.merchant_id == merchant_id, CheckoutSession.status == "ABANDONED")):
        has_case = db.scalar(select(RecoveryCase.id).where(RecoveryCase.order_id == s.order_id).limit(1))
        failed_payment = db.scalar(select(Payment.id).where(Payment.order_id == s.order_id, Payment.status.in_(["DECLINED", "FAILED"])).limit(1))
        if not has_case and not failed_payment and not policy.order_is_paid(db, s.order_id):
            case = ensure_case(db, session=s)
            if case:
                analyse_case(db, case)
                plan_actions(db, case)
                summary["cases_opened"] += 1

    # 1b. PayPal orders created/approved but never captured (approval incomplete / capture stuck)
    for p in db.scalars(select(Payment).where(Payment.merchant_id == merchant_id, Payment.status.in_(["CREATED", "APPROVED"]))):
        if status_changed_at(db, p) >= cutoff or policy.order_is_paid(db, p.order_id) or policy.order_has_pending(db, p.order_id):
            continue
        newer = db.scalar(select(Payment.id).where(Payment.order_id == p.order_id, Payment.created_at > p.created_at).limit(1))
        if newer:
            continue  # superseded by a later attempt
        had_case = db.scalar(select(RecoveryCase.id).where(RecoveryCase.order_id == p.order_id).limit(1))
        if had_case:
            continue
        case = ensure_case(db, payment=p, case_type="ABANDONED_CHECKOUT" if p.status == "CREATED" and p.checkout_session_id
                           and db.get(CheckoutSession, p.checkout_session_id).status == "ABANDONED" else "APPROVAL_INCOMPLETE")
        if case:
            analyse_case(db, case, payment=p)
            plan_actions(db, case)
            summary["cases_opened"] += 1

    # 2. stale pending payments -> merchant notification (never a new charge)
    pend_cut = now - timedelta(minutes=settings.pending_alert_after_minutes)
    for p in db.scalars(select(Payment).where(Payment.merchant_id == merchant_id, Payment.status == "PENDING")):
        if status_changed_at(db, p) < pend_cut:
            case = ensure_case(db, payment=p, case_type="PENDING_PAYMENT")
            if case and not db.scalar(select(RecoveryAction.id).where(RecoveryAction.case_id == case.id).limit(1)):
                analyse_case(db, case, payment=p)
                plan_actions(db, case)
                summary["pending_flagged"] += 1

    # 3. close cases whose order got paid by any route
    for case in db.scalars(select(RecoveryCase).where(RecoveryCase.merchant_id == merchant_id, RecoveryCase.status.in_(ACTIVE_CASE))):
        if policy.order_is_paid(db, case.order_id):
            case.status, case.closed_at = "CLOSED_ALREADY_PAID", now
            cancel_customer_actions(db, case.order_id, "Order already paid")
            summary["cases_closed_paid"] += 1

    # 4. due scheduled actions
    due = db.scalars(select(RecoveryAction).where(RecoveryAction.merchant_id == merchant_id, RecoveryAction.status == "SCHEDULED")).all()
    for a in due:
        if aware(a.scheduled_for) and aware(a.scheduled_for) > now:
            continue
        seq = (a.payload or {}).get("sequence", 1)
        if a.action_type in policy.CUSTOMER_FACING and seq > 1:
            # Follow-ups are sequential: #n waits until #n-1 was actually sent, then the configured gap.
            case = db.get(RecoveryCase, a.case_id)
            ivs = settings.reminder_intervals_minutes
            gap = (ivs[seq - 1] - ivs[seq - 2]) if len(ivs) >= seq else settings.min_minutes_between_messages
            if case.reminders_sent < seq - 1 or not case.last_contacted_at:
                continue
            next_due = aware(case.last_contacted_at) + timedelta(minutes=gap)
            if next_due > now:
                a.scheduled_for = next_due
                continue
        if settings.recovery_mode == "AUTOMATIC" and not a.requires_approval:
            case = db.get(RecoveryCase, a.case_id)
            ok, why = policy.can_contact(db, case, settings) if a.action_type in policy.CUSTOMER_FACING else (True, "")
            if not ok and why.startswith("Last message"):
                a.scheduled_for = aware(case.last_contacted_at) + timedelta(minutes=settings.min_minutes_between_messages + 1)
                continue  # respect spacing; try again later
            holds = [c for c in policy.guardrails(db, case, settings, a.action_type) if c["status"] == "hold"]
            if any(c["key"] == "quiet_hours" for c in holds):
                a.scheduled_for = now + timedelta(minutes=30)
                continue  # quiet hours: try again later
            if holds:  # over the automation amount limit or not on the allow-list -> human decides
                a.status, a.requires_approval = "PROPOSED", True
                summary["actions_awaiting_approval"] += 1
                notify(db, merchant_id, "info", "Recovery action needs approval", holds[0]["detail"], link=f"/recovery?case={case.id}", category="approval")
                continue
            execute_action(db, a, actor_type="agent", actor_id=None)
            summary["actions_executed"] += int(a.status == "EXECUTED")
        else:
            a.status = "PROPOSED"
            summary["actions_awaiting_approval"] += 1
            notify(db, merchant_id, "info", "Recovery reminder ready for approval", a.reason or a.action_type, link="/recovery", category="approval")
    # 5. reconcile payments whose outcome is uncertain (timeouts / PayPal 5xx) — never assume failure
    from . import retry as _retry
    summary["reconciled"] = _retry.reconcile_pending_uncertain(db, merchant_id)
    # 6. escalation: cases open too long get a one-time merchant escalation
    esc_cut = now - timedelta(hours=settings.escalate_after_hours or 72)
    summary["escalated"] = 0
    for case in db.scalars(select(RecoveryCase).where(RecoveryCase.merchant_id == merchant_id, RecoveryCase.status.in_(ACTIVE_CASE),
                                                      RecoveryCase.escalated_at.is_(None))):
        if aware(case.created_at) < esc_cut:
            case.escalated_at = now
            summary["escalated"] += 1
            notify(db, merchant_id, "warning", f"Escalation: {money(case.amount_minor, case.currency)} unresolved",
                   f"Case open for more than {settings.escalate_after_hours}h without recovery.", link=f"/recovery?case={case.id}", category="escalation")
            audit(db, merchant_id, "agent", "case.escalated", f"Escalated case for order after {settings.escalate_after_hours}h", entity_type="recovery_case", entity_id=case.id)
    audit(db, merchant_id, "agent", "agent.tick", "Monitoring pass: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in summary.items()),
          detail=summary)
    return summary
