"""PayPal Invoicing v2 as a recovery rail: payment confirmation via verified INVOICING.INVOICE.PAID webhook or GET /v2/invoicing/invoices/{id}."""
from sqlalchemy.orm import Session

from ..models import Order, Payment, PayPalInvoice
from . import agent
from .audit import audit, money
from . import providers


def record_paid(db: Session, inv: PayPalInvoice, resource: dict, source: str) -> Payment | None:
    if inv.status == "PAID" and inv.paid_payment_id:
        return db.get(Payment, inv.paid_payment_id)  # idempotent
    order = db.get(Order, inv.order_id)
    txns = (resource.get("payments") or {}).get("transactions") or []
    txn_id = txns[0].get("payment_id") if txns else None
    payment = Payment(merchant_id=inv.merchant_id, order_id=order.id, customer_id=order.customer_id, reference=agent.new_reference(),
                      amount_minor=inv.amount_minor, currency=inv.currency, status="CREATED", payment_method="paypal_invoice",
                      data_source=providers.paypal_data_source(db, inv.merchant_id), paypal_capture_id=txn_id or f"INV-{inv.paypal_invoice_id}",
                      raw_response={"invoice": {"id": inv.paypal_invoice_id, "status": resource.get("status")}},
                      is_recovery_attempt=True, recovery_case_id=inv.case_id)
    db.add(payment)
    db.flush()
    agent.apply_status(db, payment, "APPROVED", source=source, run_agent=False)
    agent.apply_status(db, payment, "COMPLETED", source=source, detail={"paypal_invoice_id": inv.paypal_invoice_id})
    inv.status, inv.paid_payment_id = "PAID", payment.id
    audit(db, inv.merchant_id, "paypal", "invoice.paid", f"PayPal invoice {inv.paypal_invoice_id} paid ({money(inv.amount_minor, inv.currency)}) — confirmed by {source}",
          entity_type="recovery_case", entity_id=inv.case_id)
    return payment


def sync(db: Session, inv: PayPalInvoice) -> PayPalInvoice:
    res = providers.paypal_for(db, inv.merchant_id).get_invoice(inv.paypal_invoice_id)
    status = res.get("status", inv.status)
    if status in ("PAID", "MARKED_AS_PAID"):
        record_paid(db, inv, res, "PAYPAL_API")
    else:
        inv.status = status
    db.commit()
    return inv
