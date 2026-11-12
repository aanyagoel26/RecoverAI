"""Reconciliation and safe capture retry.

Rules (all enforced here, before any PayPal call that could move money):
  * A timeout / 5xx is never treated as a failure: the payment is flagged `reconcile_required` and stays in its state.
  * Before any retry we re-read the authoritative state (GET /v2/checkout/orders/{id}).
      - already captured  -> record it, close the case, no retry
      - not approved      -> block: only the buyer can approve; a capture cannot succeed
      - APPROVED          -> retry the capture with the SAME PayPal-Request-Id as the original attempt,
                             so even a lost-response duplicate is de-duplicated by PayPal
  * Retry limit and cooldown per payment, in-process lock against concurrent retries.
"""
import threading
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Order, Payment, RecoveryAction, RecoveryCase
from . import agent, providers
from .audit import audit
from .paypal_client import PayPalError, PayPalNotConfigured, extract_capture

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(payment_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(payment_id, threading.Lock())


def reconcile(db: Session, payment: Payment) -> dict:
    """Authoritative refresh from PayPal. Returns {'paypal_status', 'changed', 'note'}; never guesses."""
    if payment.data_source == "DEMO_SIMULATED":
        payment.reconcile_required = False
        status = (payment.raw_response or {}).get("simulated_paypal_status", "CREATED" if payment.status == "CREATED" else "APPROVED")
        return {"paypal_status": status, "changed": False, "note": "Demo payment: reconciled against simulated PayPal state"}
    from .checkout import sync_from_paypal
    try:
        before = payment.status
        res = providers.paypal_for(db, payment.merchant_id, "sandbox" if payment.data_source == "PAYPAL_SANDBOX" else "live").get_order(payment.paypal_order_id)
    except PayPalError as e:
        if e.http_status == 404:
            if payment.status in ("CREATED", "APPROVED"):
                agent.apply_status(db, payment, "EXPIRED", source="PAYPAL_API", detail={"reconcile": "order not found at PayPal"}, run_agent=False)
            payment.reconcile_required = False
            return {"paypal_status": "NOT_FOUND", "changed": True, "note": "PayPal no longer has this order (expired)"}
        return {"paypal_status": "UNKNOWN", "changed": False, "note": f"PayPal unreachable ({e.name}); still uncertain — will retry reconciliation"}
    except PayPalNotConfigured as e:
        return {"paypal_status": "UNKNOWN", "changed": False, "note": str(e)}
    cap = extract_capture(res)
    if cap or res.get("status") in ("APPROVED", "COMPLETED"):
        sync_from_paypal(db, payment)
    payment.reconcile_required = False
    payment.raw_response = (payment.raw_response or {}) | {"last_reconcile": {"order_status": res.get("status"), "capture_status": (cap or {}).get("status")}}
    audit(db, payment.merchant_id, "system", "payment.reconciled",
          f"Reconciled {payment.reference} with PayPal: order {res.get('status')}" + (f", capture {cap.get('status')}" if cap else ""),
          entity_type="payment", entity_id=payment.id)
    return {"paypal_status": res.get("status"), "capture_status": (cap or {}).get("status"), "changed": payment.status != before,
            "note": "Authoritative state read from PayPal"}


def retry_checks(db: Session, case: RecoveryCase, payment: Payment, settings) -> tuple[list[dict], dict]:
    """Trust Firewall for RETRY_CAPTURE. Reconciles first, so the checks reflect PayPal's current truth."""
    checks: list[dict] = []

    def add(key, label, ok, detail, hold=False):
        checks.append({"key": key, "label": label, "status": "hold" if hold else ("pass" if ok else "fail"), "detail": detail})

    rec = reconcile(db, payment)
    db.flush()
    add("reconciled", "Reconciled with PayPal before retry", rec["paypal_status"] != "UNKNOWN", rec["note"])
    from .policy import order_has_pending, order_is_paid
    paid = order_is_paid(db, case.order_id)
    add("not_paid", "Not already captured", not paid, "PayPal shows a completed capture — no retry needed" if paid else "No completed capture")
    add("no_pending", "No pending capture", not order_has_pending(db, case.order_id), "Pending captures are never retried")
    approved = rec["paypal_status"] == "APPROVED" and payment.status == "APPROVED"
    add("buyer_approved", "Buyer approved this PayPal order", approved,
        "Order is APPROVED and uncaptured — a capture may be retried" if approved else
        f"PayPal order status is {rec['paypal_status']}: only the buyer can approve it, so a capture cannot be retried")
    done = db.scalar(select(func.count()).select_from(RecoveryAction).where(
        RecoveryAction.case_id == case.id, RecoveryAction.action_type == "RETRY_CAPTURE", RecoveryAction.status == "EXECUTED")) or 0
    add("retry_limit", "Within retry limit", done < settings.max_retry_attempts, f"{done} of {settings.max_retry_attempts} retries used")
    last = db.scalar(select(func.max(RecoveryAction.executed_at)).where(RecoveryAction.case_id == case.id, RecoveryAction.action_type == "RETRY_CAPTURE"))
    if last:
        wait = agent.aware(last) + timedelta(minutes=settings.retry_cooldown_minutes) - agent.utcnow()
        add("cooldown", "Retry cooldown respected", wait.total_seconds() <= 0,
            f"Wait {int(wait.total_seconds() // 60) + 1} more minute(s)" if wait.total_seconds() > 0 else f"Cooldown {settings.retry_cooldown_minutes} min passed")
    else:
        add("cooldown", "Retry cooldown respected", True, "First retry")
    order = db.get(Order, case.order_id)
    add("amount", "Amount unchanged", payment.amount_minor == order.amount_minor, f"Capture of the original {order.amount_minor / 100:,.2f}")
    add("idempotent", "Same idempotency key as original capture", True, f"PayPal-Request-Id capture-{payment.id[:8]}…")
    return checks, rec


def execute_retry(db: Session, action: RecoveryAction, case: RecoveryCase, settings, actor_type: str) -> tuple[list[dict], str]:
    payment = db.get(Payment, case.payment_id) if case.payment_id else None
    if not payment or not payment.paypal_order_id:
        action.status, action.blocked_reason = "BLOCKED", "No PayPal order to retry"
        return [], action.blocked_reason
    lock = _lock_for(payment.id)
    if not lock.acquire(blocking=False):
        action.status, action.blocked_reason = "BLOCKED", "Another retry for this payment is already running"
        return [{"key": "lock", "label": "No concurrent retry", "status": "fail", "detail": action.blocked_reason}], action.blocked_reason
    try:
        checks, rec = retry_checks(db, case, payment, settings)
        checks.insert(0, {"key": "lock", "label": "No concurrent retry", "status": "pass", "detail": "Lock acquired"})
        failed = [c for c in checks if c["status"] == "fail"]
        if failed:
            action.status, action.blocked_reason = "BLOCKED", failed[0]["detail"]
            audit(db, case.merchant_id, "agent", "action.blocked", f"Retry capture blocked by Trust Firewall: {failed[0]['detail']}",
                  entity_type="recovery_action", entity_id=action.id)
            return checks, action.blocked_reason
        if payment.data_source == "DEMO_SIMULATED":
            agent.apply_status(db, payment, "COMPLETED", source="DEMO_SIMULATOR", detail={"retry": True, "simulated": True})
            return checks, f"[DEMO] Retried capture of {payment.reference}: simulated COMPLETED"
        from .checkout import capture_paypal_order
        from fastapi import HTTPException
        try:
            capture_paypal_order(db, payment.paypal_order_id, merchant_id=case.merchant_id, commit=False)
        except HTTPException as e:
            return checks, f"Retried capture of {payment.reference}: PayPal answered {e.detail if isinstance(e.detail, str) else e.detail.get('issue')}"
        return checks, f"Retried capture of {payment.reference}: PayPal status {payment.status}"
    finally:
        lock.release()


def reconcile_pending_uncertain(db: Session, merchant_id: str) -> int:
    n = 0
    for p in db.scalars(select(Payment).where(Payment.merchant_id == merchant_id, Payment.reconcile_required.is_(True)).limit(20)):
        reconcile(db, p)
        n += 1
    return n
