"""PayPal webhook ingestion: store -> dedupe -> verify -> idempotent apply -> trigger agent.

Unverified events are stored for audit but never change payment state.
"""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import CheckoutSession, Payment, PayPalConnection, PayPalInvoice, WebhookEvent
from . import agent
from .audit import audit
from .paypal_client import PayPalError, PayPalNotConfigured

EVENT_STATUS = {
    "PAYMENT.CAPTURE.COMPLETED": "COMPLETED",
    "PAYMENT.CAPTURE.PENDING": "PENDING",
    "PAYMENT.CAPTURE.DENIED": "DECLINED",
    "PAYMENT.CAPTURE.DECLINED": "DECLINED",
    "PAYMENT.CAPTURE.REFUNDED": "REFUNDED",
    "PAYMENT.CAPTURE.REVERSED": "REFUNDED",
    "CHECKOUT.ORDER.APPROVED": "APPROVED",
    "CHECKOUT.ORDER.COMPLETED": None,  # informational; capture events carry the money state
}


class WebhookRejected(Exception):
    pass


def _find_payment(db: Session, event_type: str, resource: dict) -> Payment | None:
    if event_type.startswith("CHECKOUT.ORDER"):
        return db.scalar(select(Payment).where(Payment.paypal_order_id == resource.get("id")))
    cap_id = resource.get("id")
    if event_type == "PAYMENT.CAPTURE.REFUNDED" or event_type == "PAYMENT.CAPTURE.REVERSED":
        for link in resource.get("links", []):
            if link.get("rel") == "up" and "/captures/" in link.get("href", ""):
                cap_id = link["href"].rstrip("/").split("/")[-1]
    p = db.scalar(select(Payment).where(Payment.paypal_capture_id == cap_id)) if cap_id else None
    if p:
        return p
    order_id = ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
    if order_id:
        return db.scalar(select(Payment).where(Payment.paypal_order_id == order_id))
    return None


def _resolve_owner(db: Session, event_type: str, resource: dict) -> tuple[str | None, str | None]:
    """(merchant_id, environment) of the PayPal resource, without changing any state."""
    if event_type.startswith("INVOICING.INVOICE"):
        inv = db.scalar(select(PayPalInvoice).where(PayPalInvoice.paypal_invoice_id == resource.get("id")))
        return (inv.merchant_id, None) if inv else (None, None)
    p = _find_payment(db, event_type, resource)
    if not p:
        return None, None
    env = {"PAYPAL_SANDBOX": "sandbox", "PAYPAL_LIVE": "live"}.get(p.data_source)
    return p.merchant_id, env


def ingest(db: Session, headers: dict, raw_body: bytes, merchant_hint: str | None = None) -> dict:
    from . import providers
    try:
        event = json.loads(raw_body)
    except ValueError:
        raise WebhookRejected("Body is not JSON")
    event_id, event_type = event.get("id"), event.get("event_type", "")
    if not event_id or not event_type:
        raise WebhookRejected("Missing event id or type")

    existing = db.scalar(select(WebhookEvent).where(WebhookEvent.paypal_event_id == event_id))
    if existing and existing.processing_status in ("PROCESSED", "IGNORED"):
        return {"status": "duplicate", "event_id": event_id}  # PayPal retries: acknowledge without reprocessing

    resource = event.get("resource") or {}
    row = existing or WebhookEvent(paypal_event_id=event_id, event_type=event_type, resource_id=resource.get("id"),
                                   payload=event, verification_status="PENDING")
    if not existing:
        db.add(row)
        db.flush()

    owner, env = _resolve_owner(db, event_type, resource)
    mid = merchant_hint or owner
    if merchant_hint and owner and owner != merchant_hint:
        row.verification_status, row.processing_status = "REJECTED", "IGNORED"
        row.processing_error = "Resource belongs to a different merchant than the webhook URL"
        db.commit()
        raise WebhookRejected("Resource does not belong to this merchant")
    cfg = providers.paypal_config(db, mid, env)
    if not (cfg and cfg.get("webhook_id")):
        row.verification_status, row.processing_status = "SKIPPED_NOT_CONFIGURED", "IGNORED"
        row.processing_error = "PayPal credentials or webhook ID not configured for this merchant — event stored but not trusted"
        db.commit()
        return {"status": "stored_unverified", "event_id": event_id}
    try:
        verdict = providers.paypal_for(db, mid, cfg["environment"]).verify_webhook_signature(
            headers=headers, webhook_id=cfg["webhook_id"], event=event)
    except (PayPalError, PayPalNotConfigured) as e:
        row.verification_status, row.processing_status, row.processing_error = "ERROR", "ERROR", str(e)[:500]
        db.commit()
        raise WebhookRejected("Could not verify signature (PayPal will retry)")
    row.verification_status = verdict
    if verdict != "SUCCESS":
        row.processing_status, row.processing_error = "IGNORED", "Signature verification failed"
        if mid:
            row.merchant_id = mid
            agent.notify(db, mid, "warning", "Rejected a PayPal webhook", f"{event_type} failed signature verification and was ignored.",
                         link="/integrations", category="integration")
        db.commit()
        raise WebhookRejected("Invalid webhook signature")

    return _apply(db, row, event_type, resource, source="PAYPAL_WEBHOOK")


def _apply_invoice(db: Session, row: WebhookEvent, event_type: str, resource: dict, source: str) -> dict:
    from . import invoices
    inv = db.scalar(select(PayPalInvoice).where(PayPalInvoice.paypal_invoice_id == resource.get("id")))
    if not inv:
        row.processing_status, row.processing_error, row.processed_at = "IGNORED", "Invoice not created by RecoverAI", agent.utcnow()
        db.commit()
        return {"status": "ignored", "reason": "unknown invoice"}
    row.merchant_id = inv.merchant_id
    if event_type == "INVOICING.INVOICE.PAID":
        p = invoices.record_paid(db, inv, resource, source)
        result = {"status": "processed", "payment_id": p.id if p else None, "new_status": "COMPLETED"}
    else:
        inv.status = resource.get("status") or event_type.split(".")[-1]
        result = {"status": "processed", "invoice_status": inv.status}
    conn = db.scalar(select(PayPalConnection).where(PayPalConnection.merchant_id == inv.merchant_id))
    if conn and source == "PAYPAL_WEBHOOK":
        conn.last_webhook_at = agent.utcnow()
    row.processing_status, row.processed_at = "PROCESSED", agent.utcnow()
    db.commit()
    return result


def _apply(db: Session, row: WebhookEvent, event_type: str, resource: dict, *, source: str) -> dict:
    if event_type.startswith("INVOICING.INVOICE"):
        return _apply_invoice(db, row, event_type, resource, source)
    payment = _find_payment(db, event_type, resource)
    if not payment:
        row.processing_status, row.processing_error = "IGNORED", "No matching payment for this resource"
        row.processed_at = agent.utcnow()
        db.commit()
        return {"status": "ignored", "reason": "no matching payment"}
    row.merchant_id = payment.merchant_id
    target = EVENT_STATUS.get(event_type)
    try:
        if event_type.startswith("PAYMENT.CAPTURE") and event_type not in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
            payment.paypal_capture_id = payment.paypal_capture_id or resource.get("id")
            payment.raw_response = (payment.raw_response or {}) | {"capture": resource}
        if target:
            if target in ("COMPLETED", "PENDING", "DECLINED") and payment.status == "CREATED":
                agent.apply_status(db, payment, "APPROVED", source=source, run_agent=False)
            reason = (resource.get("status_details") or {}).get("reason")
            if event_type == "PAYMENT.CAPTURE.DENIED" and not reason:
                reason = "CAPTURE_DENIED"
            if target == "REFUNDED":
                try:
                    refunded = int(round(float((resource.get("amount") or {}).get("value", "0")) * 100))
                except ValueError:
                    refunded = 0
                payment.refunded_minor = min(payment.amount_minor, (payment.refunded_minor or 0) + refunded) if refunded else payment.amount_minor
                if refunded and payment.refunded_minor < payment.amount_minor:
                    target = "PARTIALLY_REFUNDED"
            agent.apply_status(db, payment, target, source=source, issue=reason,
                               detail={"webhook_event_id": row.paypal_event_id, "event_type": event_type})
        if event_type == "CHECKOUT.ORDER.APPROVED" and payment.checkout_session_id:
            sess = db.get(CheckoutSession, payment.checkout_session_id)
            sess.paypal_order_approved = True
        conn = db.scalar(select(PayPalConnection).where(PayPalConnection.merchant_id == payment.merchant_id))
        if conn and source == "PAYPAL_WEBHOOK":
            conn.last_webhook_at = agent.utcnow()
        row.processing_status, row.processed_at = "PROCESSED", agent.utcnow()
        audit(db, payment.merchant_id, "paypal" if source == "PAYPAL_WEBHOOK" else "system", "webhook.processed",
              f"{'Verified PayPal' if source == 'PAYPAL_WEBHOOK' else '[DEMO] Simulated'} event {event_type} applied to {payment.reference}",
              entity_type="payment", entity_id=payment.id)
        db.commit()
    except Exception as e:  # keep the event for replay
        db.rollback()
        row = db.merge(row)
        row.processing_status, row.processing_error = "ERROR", str(e)[:500]
        db.commit()
        raise
    return {"status": "processed", "payment_id": payment.id, "new_status": payment.status}


def simulate(db: Session, payment: Payment, event_type: str) -> dict:
    """DEMO ONLY: replay a webhook-shaped event against a DEMO_SIMULATED payment. Marked is_simulated."""
    if payment.data_source != "DEMO_SIMULATED":
        raise WebhookRejected("Simulated events can only target demo payments")
    import uuid
    resource = {"id": payment.paypal_capture_id or f"DEMOCAP-{uuid.uuid4().hex[:12].upper()}", "status": event_type.split(".")[-1],
                "supplementary_data": {"related_ids": {"order_id": payment.paypal_order_id}}}
    if event_type.startswith("CHECKOUT.ORDER"):
        resource = {"id": payment.paypal_order_id, "status": "APPROVED"}
    event_id = f"DEMO-WH-{uuid.uuid4().hex[:16].upper()}"
    row = WebhookEvent(paypal_event_id=event_id, event_type=event_type, resource_id=resource["id"], merchant_id=payment.merchant_id,
                       payload={"id": event_id, "event_type": event_type, "resource": resource, "simulated": True},
                       verification_status="DEMO", is_simulated=True)
    db.add(row)
    db.flush()
    return _apply(db, row, event_type, resource, source="DEMO_SIMULATOR") | {"event_id": event_id}


def replay_duplicate(db: Session, event_id: str) -> dict:
    """Demonstrates dedupe: re-delivering a processed event is acknowledged without side effects."""
    row = db.scalar(select(WebhookEvent).where(WebhookEvent.paypal_event_id == event_id))
    if not row:
        raise WebhookRejected("Unknown event")
    if row.processing_status in ("PROCESSED", "IGNORED"):
        audit(db, row.merchant_id, "system", "duplicate.webhook", f"Duplicate delivery of {row.event_type} ({event_id}) ignored")
        db.commit()
        return {"status": "duplicate", "event_id": event_id}
    return {"status": "not_processed_yet"}
