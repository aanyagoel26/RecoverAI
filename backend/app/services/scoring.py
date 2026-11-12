"""Explainable recovery-probability scoring.

This is NOT a trained ML model. It is a transparent weighted model:
  base rate per category (prior assumption), blended with this merchant's observed outcomes per category
  (Bayesian smoothing, so it 'learns' as real results accumulate), then adjusted by documented factors.
Each factor is returned so the UI can show exactly why a score is what it is.
"""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Payment, RecoveryCase

# Prior assumptions (clearly labelled as assumptions in the UI)
PRIOR_RATES = {
    "AUTHENTICATION_REQUIRED": 0.70, "APPROVAL_INCOMPLETE": 0.55, "CHECKOUT_ABANDONED": 0.25,
    "INSUFFICIENT_FUNDS": 0.35, "CARD_DECLINED": 0.45, "CAPTURE_FAILED": 0.50, "TECHNICAL_ERROR": 0.65,
    "CONFIGURATION_ISSUE": 0.30, "DUPLICATE_ATTEMPT": 0.0, "UNKNOWN": 0.30,
}
PRIOR_WEIGHT = 10  # pseudo-observations behind each prior


def observed_rate(db: Session, merchant_id: str, category: str) -> tuple[int, int]:
    rows = db.execute(
        select(RecoveryCase.status, func.count()).where(
            RecoveryCase.merchant_id == merchant_id, RecoveryCase.failure_category == category,
            RecoveryCase.status.in_(["RECOVERED", "CLOSED_UNRECOVERED"]))
        .group_by(RecoveryCase.status)).all()
    d = dict(rows)
    won, lost = d.get("RECOVERED", 0), d.get("CLOSED_UNRECOVERED", 0)
    return won, won + lost


def recovery_probability(db: Session, merchant_id: str, *, category: str, amount_minor: int, customer_id: str,
                         failed_at: datetime, attempts_for_order: int, has_consent: bool) -> tuple[float, list[dict]]:
    factors: list[dict] = []
    prior = PRIOR_RATES.get(category, 0.3)
    won, n = observed_rate(db, merchant_id, category)
    base = (prior * PRIOR_WEIGHT + won) / (PRIOR_WEIGHT + n)
    factors.append({"factor": "Category base rate", "effect": round(base, 3),
                    "detail": f"Prior assumption {prior:.0%} blended with {won}/{n} of this merchant's closed cases"})
    p = base
    if category == "DUPLICATE_ATTEMPT":
        return 0.0, factors + [{"factor": "Duplicate", "effect": -1, "detail": "Already processed; nothing to recover"}]

    # Customer history: prior completed payments raise likelihood
    completed = db.scalar(select(func.count()).select_from(Payment).where(
        Payment.merchant_id == merchant_id, Payment.customer_id == customer_id, Payment.status == "COMPLETED")) or 0
    if completed:
        adj = min(0.04 * completed, 0.12)
        p += adj
        factors.append({"factor": "Customer history", "effect": round(adj, 3), "detail": f"{completed} previous completed payment(s)"})

    # Time decay
    if failed_at.tzinfo is None:
        failed_at = failed_at.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - failed_at).total_seconds() / 3600
    if hours > 72:
        adj = -0.20
    elif hours > 24:
        adj = -0.10
    elif hours < 2:
        adj = 0.05
    else:
        adj = 0.0
    if adj:
        p += adj
        factors.append({"factor": "Time since failure", "effect": adj, "detail": f"{hours:.0f}h elapsed"})

    # Repeated attempts
    if attempts_for_order >= 3:
        p -= 0.10
        factors.append({"factor": "Repeated failures", "effect": -0.10, "detail": f"{attempts_for_order} attempts on this order"})

    # Amount band (large baskets slightly less likely to be retried impulsively)
    if amount_minor >= 100_000:
        p -= 0.05
        factors.append({"factor": "Order value", "effect": -0.05, "detail": "High-value order (≥ 1,000)"})

    if not has_consent:
        p -= 0.20
        factors.append({"factor": "No contact consent", "effect": -0.20, "detail": "Customer cannot be messaged; merchant must follow up manually"})

    p = max(0.02, min(0.95, p))
    return round(p, 3), factors


def risk_level(p: float) -> str:
    return "LOW" if p >= 0.6 else "MEDIUM" if p >= 0.35 else "HIGH"


def priority(amount_minor: int, p: float, hours_open: float, category: str, needs_approval: bool) -> tuple[float, str]:
    expected_value = amount_minor / 100 * p
    urgency = 1.3 if hours_open < 24 else 1.0 if hours_open < 72 else 0.7  # recover while intent is fresh
    score = expected_value * urgency
    reasons = [f"expected recoverable value {expected_value:,.0f} ({p:.0%} × {amount_minor / 100:,.0f})"]
    if hours_open < 24:
        reasons.append("still within the first 24h when recovery is most likely")
    elif hours_open > 72:
        reasons.append(f"open {hours_open / 24:.0f} days — intent is fading")
    if category == "AUTHENTICATION_REQUIRED":
        reasons.append("customer only needs to complete one step")
    if needs_approval:
        reasons.append("awaiting your approval")
    return round(score, 2), "; ".join(reasons)
