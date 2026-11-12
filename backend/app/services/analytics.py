"""All numbers are computed from database rows. Nothing here is hand-entered or produced by an LLM."""
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CheckoutSession, Customer, Payment, RecoveryAction, RecoveryCase, RevenuePrediction
from .scoring import PRIOR_RATES

ACTIVE = ("OPEN", "AWAITING_APPROVAL", "IN_PROGRESS")


def window(days: int | None = 30, start: str | None = None, end: str | None = None):
    now = datetime.now(timezone.utc)
    if start:
        s = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        e = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=1) if end else now
        return s, e
    if days == 0:  # today
        return now.replace(hour=0, minute=0, second=0, microsecond=0), now
    return now - timedelta(days=days or 30), now


def _aware(d):
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def overview(db: Session, mid: str, *, days: int | None = 30, start=None, end=None, source: str | None = None) -> dict:
    s, e = window(days, start, end)
    pq = select(Payment).where(Payment.merchant_id == mid)
    if source:
        pq = pq.where(Payment.data_source == source)
    payments = [p for p in db.scalars(pq) if s <= _aware(p.created_at) <= e]
    cq = select(RecoveryCase).where(RecoveryCase.merchant_id == mid)
    if source:
        cq = cq.where(RecoveryCase.data_source == source)
    cases_all = db.scalars(cq).all()
    cases = [c for c in cases_all if s <= _aware(c.created_at) <= e]
    sessions = [x for x in db.scalars(select(CheckoutSession).where(CheckoutSession.merchant_id == mid)) if s <= _aware(x.created_at) <= e
                and (not source or x.data_source == source)]

    st = Counter(p.status for p in payments)
    completed, failed = st["COMPLETED"], st["DECLINED"] + st["FAILED"]
    recovered = [c for c in cases_all if c.status == "RECOVERED" and c.recovered_at and s <= _aware(c.recovered_at) <= e]
    active = [c for c in cases_all if c.status in ACTIVE]
    closed = [c for c in cases if c.status in ("RECOVERED", "CLOSED_UNRECOVERED")]
    needs_customer = [c for c in active if c.failure_category in ("AUTHENTICATION_REQUIRED", "CARD_DECLINED", "INSUFFICIENT_FUNDS",
                                                                  "CHECKOUT_ABANDONED", "APPROVAL_INCOMPLETE")]
    rec_times = [(_aware(c.recovered_at) - _aware(c.created_at)).total_seconds() / 3600 for c in recovered]
    currency = payments[0].currency if payments else "USD"
    terminal = completed + failed
    return {
        "window": {"start": s.isoformat(), "end": e.isoformat()},
        "currency": currency,
        "total_attempts": len(payments),
        "successful": completed,
        "failed": failed,
        "pending": st["PENDING"],
        "approval_incomplete": st["CREATED"] + st["APPROVED"],
        "abandoned_sessions": sum(1 for x in sessions if x.status == "ABANDONED"),
        "requires_customer_action": len(needs_customer),
        "recovered_count": len(recovered),
        "revenue_processed_minor": sum(p.amount_minor for p in payments if p.status == "COMPLETED"),
        "revenue_recovered_minor": sum(c.recovered_amount_minor for c in recovered),
        "revenue_at_risk_minor": sum(c.amount_minor for c in active),
        "success_rate": round(completed / terminal, 4) if terminal else None,
        "recovery_conversion_rate": round(sum(1 for c in closed if c.status == "RECOVERED") / len(closed), 4) if closed else None,
        "avg_hours_to_recovery": round(sum(rec_times) / len(rec_times), 1) if rec_times else None,
        "open_cases": len(active),
        "recovered_by_source": dict(Counter(c.data_source if not c.recovered_payment_id else
                                            (db.get(Payment, c.recovered_payment_id).data_source if db.get(Payment, c.recovered_payment_id) else c.data_source)
                                            for c in recovered)),
        "by_source": dict(Counter(p.data_source for p in payments)),
        "definitions": {
            "success_rate": "completed ÷ (completed + declined + failed) payment attempts in the window",
            "recovery_conversion_rate": "recovered ÷ closed recovery cases (recovered + closed unrecovered)",
            "revenue_recovered": "sum of payments completed for orders that had an open RecoverAI case, confirmed by PayPal API/webhook (or demo simulator for demo data)",
            "revenue_at_risk": "order value of all currently open recovery cases",
        },
    }


def timeseries(db: Session, mid: str, *, days: int | None = 30, start=None, end=None) -> list[dict]:
    s, e = window(days, start, end)
    buckets: dict[str, dict] = {}
    d = s.date()
    while d <= e.date():
        buckets[d.isoformat()] = {"day": d.isoformat(), "attempts": 0, "completed": 0, "failed": 0, "volume_minor": 0,
                                  "recovered_minor": 0, "at_risk_opened_minor": 0}
        d += timedelta(days=1)
    for p in db.scalars(select(Payment).where(Payment.merchant_id == mid)):
        k = _aware(p.created_at).date().isoformat()
        if k in buckets:
            b = buckets[k]
            b["attempts"] += 1
            b["completed"] += p.status == "COMPLETED"
            b["failed"] += p.status in ("DECLINED", "FAILED")
            b["volume_minor"] += p.amount_minor if p.status == "COMPLETED" else 0
    for c in db.scalars(select(RecoveryCase).where(RecoveryCase.merchant_id == mid)):
        k = _aware(c.created_at).date().isoformat()
        if k in buckets:
            buckets[k]["at_risk_opened_minor"] += c.amount_minor
        if c.status == "RECOVERED" and c.recovered_at:
            k2 = _aware(c.recovered_at).date().isoformat()
            if k2 in buckets:
                buckets[k2]["recovered_minor"] += c.recovered_amount_minor
    return list(buckets.values())


def breakdowns(db: Session, mid: str, *, days: int | None = 30, start=None, end=None) -> dict:
    s, e = window(days, start, end)
    cases = [c for c in db.scalars(select(RecoveryCase).where(RecoveryCase.merchant_id == mid)) if s <= _aware(c.created_at) <= e]
    pays = [p for p in db.scalars(select(Payment).where(Payment.merchant_id == mid)) if s <= _aware(p.created_at) <= e]
    cats = Counter(p.failure_category or "UNCLASSIFIED" for p in pays if p.status in ("DECLINED", "FAILED"))
    # strategy = the customer-facing action used on the case
    strat: dict[str, dict] = defaultdict(lambda: {"cases": 0, "recovered": 0, "recovered_minor": 0})
    for c in cases:
        acts = db.scalars(select(RecoveryAction.action_type).where(RecoveryAction.case_id == c.id, RecoveryAction.status == "EXECUTED")).all()
        key = next((a for a in acts if a in ("SEND_REMINDER", "REQUEST_CUSTOMER_ACTION", "RECOMMEND_ALTERNATIVE_METHOD")), None) or (acts[0] if acts else "NO_ACTION_YET")
        n_msgs = sum(1 for a in acts if a in ("SEND_REMINDER", "REQUEST_CUSTOMER_ACTION", "RECOMMEND_ALTERNATIVE_METHOD"))
        if n_msgs > 1:
            key += f" ×{n_msgs}"
        strat[key]["cases"] += 1
        if c.status == "RECOVERED":
            strat[key]["recovered"] += 1
            strat[key]["recovered_minor"] += c.recovered_amount_minor
    cust = defaultdict(lambda: {"attempts": 0, "completed": 0, "failed": 0})
    for p in pays:
        x = cust[p.customer_id]
        x["attempts"] += 1
        x["completed"] += p.status == "COMPLETED"
        x["failed"] += p.status in ("DECLINED", "FAILED")
    names = {c.id: c.name for c in db.scalars(select(Customer).where(Customer.merchant_id == mid))}
    customers = sorted(({"customer": names.get(k, k), **v} for k, v in cust.items()), key=lambda r: (-r["failed"], -r["attempts"]))[:10]
    return {
        "failure_categories": [{"category": k, "count": v} for k, v in cats.most_common()],
        "payment_status": [{"status": k, "count": v} for k, v in Counter(p.status for p in pays).most_common()],
        "strategies": [{"strategy": k, **v, "rate": round(v["recovered"] / v["cases"], 3) if v["cases"] else 0} for k, v in strat.items()],
        "customers": customers,
        "case_status": [{"status": k, "count": v} for k, v in Counter(c.status for c in cases).most_common()],
    }


# ---------------------------------------------------------------- revenue rescue simulator
DEFAULT_SCENARIOS = {
    "A": {"label": "One reminder", "reminders": 1, "checkout_assist": False},
    "B": {"label": "Two personalised reminders", "reminders": 2, "checkout_assist": False},
    "C": {"label": "Reminder + checkout assistance", "reminders": 2, "checkout_assist": True},
}


def at_risk_pool(db: Session, mid: str) -> dict:
    active = db.scalars(select(RecoveryCase).where(RecoveryCase.merchant_id == mid, RecoveryCase.status.in_(ACTIVE))).all()
    consent = {c.id: c.recovery_contact_consent for c in db.scalars(select(Customer).where(Customer.merchant_id == mid))}
    pool = {"failed_minor": 0, "abandoned_minor": 0, "pending_minor": 0, "contactable_minor": 0, "cases": len(active)}
    for c in active:
        k = "abandoned_minor" if c.case_type == "ABANDONED_CHECKOUT" else "pending_minor" if c.case_type == "PENDING_PAYMENT" else "failed_minor"
        pool[k] += c.amount_minor
        if consent.get(c.customer_id) and c.case_type != "PENDING_PAYMENT":
            pool["contactable_minor"] += c.amount_minor
    pool["total_minor"] = pool["failed_minor"] + pool["abandoned_minor"] + pool["pending_minor"]
    closed = db.scalars(select(RecoveryCase.status).where(RecoveryCase.merchant_id == mid, RecoveryCase.status.in_(["RECOVERED", "CLOSED_UNRECOVERED"]))).all()
    pool["history_closed"] = len(closed)
    pool["history_recovered"] = sum(1 for x in closed if x == "RECOVERED")
    return pool


def simulate(db: Session, mid: str, *, scenarios: dict | None = None, assumptions: dict | None = None, at_risk_override_minor: int | None = None) -> dict:
    pool = at_risk_pool(db, mid)
    a = {"failed_base_rate": PRIOR_RATES["CARD_DECLINED"], "abandoned_base_rate": PRIOR_RATES["CHECKOUT_ABANDONED"],
         "pending_resolution_rate": 0.85, "second_reminder_uplift": 0.25, "third_reminder_uplift": 0.10,
         "checkout_assist_uplift": 0.15, **(assumptions or {})}
    n, won = pool["history_closed"], pool["history_recovered"]
    if n >= 5:  # use merchant's own history to scale assumptions
        observed = won / n
        prior_mix = (a["failed_base_rate"] + a["abandoned_base_rate"]) / 2
        scale = max(0.3, min(2.0, (observed * n + prior_mix * 10) / (n + 10) / prior_mix))
        basis = f"Scaled by this merchant's history: {won}/{n} closed cases recovered ({observed:.0%})"
    else:
        scale, basis = 1.0, "Insufficient history (<5 closed cases) — using stated assumptions only"
    failed, abandoned, pending = pool["failed_minor"], pool["abandoned_minor"], pool["pending_minor"]
    if at_risk_override_minor:
        tot = pool["total_minor"] or 1
        f = at_risk_override_minor / tot
        failed, abandoned, pending = (int(failed * f), int(abandoned * f), int(pending * f)) if pool["total_minor"] else (int(at_risk_override_minor * .5), int(at_risk_override_minor * .4), int(at_risk_override_minor * .1))
    contact_share = (pool["contactable_minor"] / max(1, pool["failed_minor"] + pool["abandoned_minor"])) if pool["total_minor"] else 0.9

    results = []
    for key, sc in (scenarios or DEFAULT_SCENARIOS).items():
        mult = 1.0
        if sc.get("reminders", 1) >= 2:
            mult += a["second_reminder_uplift"]
        if sc.get("reminders", 1) >= 3:
            mult += a["third_reminder_uplift"]
        if sc.get("checkout_assist"):
            mult += a["checkout_assist_uplift"]
        rf = min(0.95, a["failed_base_rate"] * scale * mult)
        ra = min(0.95, a["abandoned_base_rate"] * scale * mult)
        exp = failed * contact_share * rf + abandoned * contact_share * ra + pending * a["pending_resolution_rate"]
        attempted = (failed + abandoned) * contact_share + pending
        width = 0.15 if n >= 30 else 0.25 if n >= 5 else 0.40
        if n >= 5:  # Wilson-style width from sample size
            width = min(width, 1.96 * math.sqrt(max(0.05, (won / n) * (1 - won / n)) / n) / max(0.05, won / n))
        res = {"key": key, "label": sc.get("label", key), "at_risk_minor": failed + abandoned + pending,
               "attempted_minor": int(attempted), "expected_minor": int(exp), "low_minor": int(exp * (1 - width)),
               "high_minor": int(min(failed + abandoned + pending, exp * (1 + width))),
               "rates": {"failed": round(rf, 3), "abandoned": round(ra, 3), "pending": a["pending_resolution_rate"]}}
        results.append(res)
        db.add(RevenuePrediction(merchant_id=mid, scenario=key, inputs={"scenario": sc, "assumptions": a}, at_risk_minor=res["at_risk_minor"],
                                 expected_minor=res["expected_minor"], low_minor=res["low_minor"], high_minor=res["high_minor"],
                                 assumptions=[basis]))
    db.commit()
    return {"pool": pool, "assumptions": a, "basis": basis, "contactable_share": round(contact_share, 3), "scenarios": results,
            "disclaimer": "These are estimates, not recovered revenue. Actual recovered revenue only counts PayPal-confirmed payments."}
