"""Customer checkout against PayPal Orders v2, plus the labelled demo simulator.

Rules enforced here:
  * never create a PayPal order for an order that is already paid,
  * reuse a still-usable PayPal order instead of creating duplicates,
  * never capture if another completed payment exists for the order,
  * the capture response from PayPal's API (or a verified webhook) is the only proof of payment — never a redirect.
"""
import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import CheckoutSession, Merchant, Order, Payment, PayPalConnection
from . import agent, policy
from .audit import audit
from . import providers
from .paypal_client import PayPalError, PayPalNotConfigured, approve_link, extract_capture

REUSE_WINDOW = timedelta(hours=3)


def paypal_source(db: Session, merchant_id: str) -> str:
    return providers.paypal_data_source(db, merchant_id)


def _pp(db: Session, merchant_id: str, data_source: str | None = None):
    env = None
    if data_source in ("PAYPAL_SANDBOX", "PAYPAL_LIVE"):
        env = "sandbox" if data_source == "PAYPAL_SANDBOX" else "live"  # always talk to the environment the payment lives in
    return providers.paypal_for(db, merchant_id, env)


def touch_connection(db: Session, merchant_id: str, ok: bool, error: str | None = None):
    conn = db.scalar(select(PayPalConnection).where(PayPalConnection.merchant_id == merchant_id))
    if not conn:
        conn = PayPalConnection(merchant_id=merchant_id)
        db.add(conn)
    cfg = providers.paypal_config(db, merchant_id)
    conn.environment = providers.paypal_environment(db, merchant_id)
    conn.webhook_id_configured = bool(cfg and cfg.get("webhook_id"))
    if ok:
        conn.status, conn.last_success_at, conn.last_error = "CONNECTED", agent.utcnow(), None
    else:
        conn.status, conn.last_error = "ERROR", (error or "")[:500]


def create_paypal_order(db: Session, order: Order, session: CheckoutSession | None, *, recovery_case_id: str | None = None) -> dict:
    if policy.order_is_paid(db, order.id):
        raise HTTPException(409, "This order has already been paid. No new payment is needed.")
    if policy.order_has_pending(db, order.id):
        raise HTTPException(409, "A payment for this order is pending at PayPal. Please wait for it to complete.")
    s = get_settings()
    if not providers.paypal_config(db, order.merchant_id):
        raise HTTPException(503, "PayPal is not connected for this merchant. Add credentials in Integrations.")

    # Duplicate-order prevention: reuse a recent unapproved/approved PayPal order for the same order
    recent = db.scalar(select(Payment).where(Payment.order_id == order.id, Payment.status.in_(["CREATED", "APPROVED"]),
                                             Payment.paypal_order_id.is_not(None)).order_by(Payment.created_at.desc()).limit(1))
    if recent and agent.utcnow() - agent.aware(recent.created_at) < REUSE_WINDOW and not recent.paypal_issue:
        audit(db, order.merchant_id, "system", "duplicate.order_reused",
              f"Reused existing PayPal order {recent.paypal_order_id} for {order.order_number} instead of creating a duplicate",
              entity_type="payment", entity_id=recent.id)
        return {"paypal_order_id": recent.paypal_order_id, "payment_id": recent.id, "reused": True,
                "approve_url": (recent.raw_response or {}).get("approve_url")}

    merchant = db.get(Merchant, order.merchant_id)
    settings = agent.merchant_settings(db, order.merchant_id)
    payment = Payment(merchant_id=order.merchant_id, order_id=order.id, customer_id=order.customer_id,
                      checkout_session_id=session.id if session else None, reference=agent.new_reference(),
                      amount_minor=order.amount_minor, currency=order.currency, status="CREATED",
                      data_source=paypal_source(db, order.merchant_id), paypal_request_id=str(uuid.uuid4()),
                      is_recovery_attempt=bool(recovery_case_id), recovery_case_id=recovery_case_id)
    db.add(payment)
    db.flush()
    pay_url = f"{s.frontend_url.rstrip('/')}/pay/{session.recovery_token}" if session and session.recovery_token else s.frontend_url
    try:
        res = _pp(db, order.merchant_id).create_order(
            amount_minor=order.amount_minor, currency=order.currency, reference_id=order.order_number,
            custom_id=order.id, invoice_id=f"{order.order_number}-{payment.id[:10]}", description=order.description,
            brand_name=settings.brand_name or merchant.name, return_url=pay_url + "?paypal=return",
            cancel_url=pay_url + "?paypal=cancel", request_id=payment.paypal_request_id)
    except PayPalNotConfigured as e:
        raise HTTPException(503, str(e))
    except PayPalError as e:
        payment.paypal_debug_id = e.debug_id
        payment.raw_response = {"error": e.raw, "debug_id": e.debug_id}
        touch_connection(db, order.merchant_id, e.http_status not in (0, 401), str(e))
        agent.apply_status(db, payment, "FAILED", source="PAYPAL_API", issue=e.issue or e.name,
                           detail={"http": e.http_status, "debug_id": e.debug_id})
        db.commit()
        raise HTTPException(502, f"PayPal could not create the order: {e.message}")
    payment.paypal_order_id = res["id"]
    payment.raw_response = {"create_status": res.get("status"), "approve_url": approve_link(res)}
    if session:
        session.paypal_order_created, session.last_activity_at = True, agent.utcnow()
    touch_connection(db, order.merchant_id, True)
    audit(db, order.merchant_id, "system", "paypal.order_created",
          f"Created PayPal order {res['id']} for {order.order_number} ({payment.reference})", entity_type="payment", entity_id=payment.id)
    db.commit()
    return {"paypal_order_id": res["id"], "payment_id": payment.id, "reused": False, "approve_url": approve_link(res)}


def _status_from_capture(capture: dict | None) -> tuple[str, str | None]:
    if not capture:
        return "APPROVED", None
    st = capture.get("status")
    reason = (capture.get("status_details") or {}).get("reason")
    return {"COMPLETED": ("COMPLETED", None), "PENDING": ("PENDING", reason), "DECLINED": ("DECLINED", reason or "CAPTURE_DECLINED"),
            "FAILED": ("FAILED", reason or "CAPTURE_FAILED"), "REFUNDED": ("REFUNDED", None),
            "PARTIALLY_REFUNDED": ("PARTIALLY_REFUNDED", None)}.get(st, ("APPROVED", st))


def capture_paypal_order(db: Session, paypal_order_id: str, merchant_id: str | None = None, commit: bool = True) -> Payment:
    q = select(Payment).where(Payment.paypal_order_id == paypal_order_id)
    if merchant_id:
        q = q.where(Payment.merchant_id == merchant_id)
    payment = db.scalar(q)
    if not payment:
        raise HTTPException(404, "Unknown PayPal order")
    if payment.status == "COMPLETED":
        return payment  # idempotent: never capture twice
    others = db.scalar(select(Payment.id).where(Payment.order_id == payment.order_id, Payment.status == "COMPLETED",
                                                Payment.id != payment.id).limit(1))
    if others:
        audit(db, payment.merchant_id, "system", "duplicate.capture_blocked",
              f"Blocked capture of {paypal_order_id}: order already has a completed payment", entity_type="payment", entity_id=payment.id)
        db.commit()
        raise HTTPException(409, "This order was already paid. The second payment was not captured.")
    try:
        res = _pp(db, payment.merchant_id, payment.data_source).capture_order(paypal_order_id, request_id=f"capture-{payment.id}")
    except PayPalError as e:
        if e.issue == "ORDER_ALREADY_CAPTURED":
            return sync_from_paypal(db, payment)
        uncertain = e.http_status == 0 or e.http_status >= 500
        payment.paypal_issue, payment.paypal_debug_id = e.issue or e.name, e.debug_id
        payment.raw_response = (payment.raw_response or {}) | {"error": e.raw, "debug_id": e.debug_id}
        if uncertain:
            # A timeout or PayPal 5xx is NOT proof of failure — the capture may have succeeded. Flag for reconciliation.
            payment.reconcile_required = True
        db.add(agent.PaymentEvent(merchant_id=payment.merchant_id, payment_id=payment.id, event_type="capture.error",
                                  source="PAYPAL_API", from_status=payment.status, to_status=payment.status,
                                  detail={"issue": e.issue, "name": e.name, "debug_id": e.debug_id, "http": e.http_status}))
        audit(db, payment.merchant_id, "paypal", "paypal.capture_error", f"Capture of {payment.reference} returned {e.issue or e.name}",
              entity_type="payment", entity_id=payment.id)
        touch_connection(db, payment.merchant_id, True)
        if commit:
            db.commit()
        if uncertain:
            raise HTTPException(502, {"issue": e.issue or e.name, "message": "PayPal did not confirm the outcome. RecoverAI will reconcile with PayPal "
                                      "before doing anything else — please don't pay again.", "debug_id": e.debug_id, "uncertain": True})
        raise HTTPException(422, {"issue": e.issue or e.name, "message": e.message, "debug_id": e.debug_id})
    capture = extract_capture(res)
    status, reason = _status_from_capture(capture)
    if capture:
        payment.paypal_capture_id = capture.get("id")
        payment.raw_response = (payment.raw_response or {}) | {"capture": capture, "order_status": res.get("status")}
    touch_connection(db, payment.merchant_id, True)
    if status == "APPROVED" and payment.status == "CREATED":
        agent.apply_status(db, payment, "APPROVED", source="PAYPAL_API")
    elif status != "APPROVED":
        if payment.status == "CREATED":
            agent.apply_status(db, payment, "APPROVED", source="PAYPAL_API", run_agent=False)
        agent.apply_status(db, payment, status, source="PAYPAL_API", issue=reason,
                           detail={"capture_id": payment.paypal_capture_id, "paypal_status": capture and capture.get("status")})
    payment.reconcile_required = False
    if commit:
        db.commit()
    return payment


def sync_from_paypal(db: Session, payment: Payment) -> Payment:
    """Authoritative refresh: GET /v2/checkout/orders/{id}."""
    res = _pp(db, payment.merchant_id, payment.data_source).get_order(payment.paypal_order_id)
    touch_connection(db, payment.merchant_id, True)
    capture = extract_capture(res)
    if capture:
        payment.paypal_capture_id = payment.paypal_capture_id or capture.get("id")
        payment.raw_response = (payment.raw_response or {}) | {"capture": capture, "order_status": res.get("status")}
        status, reason = _status_from_capture(capture)
        if payment.status == "CREATED":
            agent.apply_status(db, payment, "APPROVED", source="PAYPAL_API", run_agent=False)
        agent.apply_status(db, payment, status, source="PAYPAL_API", issue=reason)
    elif res.get("status") == "APPROVED" and payment.status == "CREATED":
        agent.apply_status(db, payment, "APPROVED", source="PAYPAL_API")
    db.commit()
    return payment


def simulate_customer_payment(db: Session, order: Order, session: CheckoutSession | None, outcome: str) -> Payment:
    """DEMO ONLY: produces a DEMO_SIMULATED payment. Refused for any order that is not demo data."""
    if order.data_source != "DEMO_SIMULATED":
        raise HTTPException(403, "The simulator only works on clearly labelled demo orders.")
    if policy.order_is_paid(db, order.id):
        raise HTTPException(409, "This order has already been paid. No new payment is needed.")
    payment = Payment(merchant_id=order.merchant_id, order_id=order.id, customer_id=order.customer_id,
                      checkout_session_id=session.id if session else None, reference=agent.new_reference(),
                      amount_minor=order.amount_minor, currency=order.currency, status="CREATED", data_source="DEMO_SIMULATED",
                      paypal_order_id=f"DEMO-{uuid.uuid4().hex[:17].upper()}")
    db.add(payment)
    db.flush()
    agent.apply_status(db, payment, "APPROVED", source="DEMO_SIMULATOR", run_agent=False)
    target = {"complete": "COMPLETED", "pending": "PENDING", "decline": "DECLINED"}[outcome]
    issue = "INSTRUMENT_DECLINED" if outcome == "decline" else ("PENDING_REVIEW" if outcome == "pending" else None)
    if issue:
        payment.raw_response = {"simulated": True, "issue": issue}
    if outcome == "complete":
        payment.paypal_capture_id = f"DEMOCAP-{uuid.uuid4().hex[:12].upper()}"
    agent.apply_status(db, payment, target, source="DEMO_SIMULATOR", issue=issue, detail={"simulated": True})
    if session:
        session.paypal_order_created = session.paypal_order_approved = True
        session.paypal_order_captured = outcome == "complete"
    audit(db, order.merchant_id, "system", "demo.simulated_payment",
          f"[DEMO] Simulated customer payment → {target} for {order.order_number}", entity_type="payment", entity_id=payment.id)
    db.commit()
    return payment
