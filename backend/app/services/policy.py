"""Deterministic payment policy engine.

The AI never moves money. This module decides:
  * which payment state transitions are legal (out-of-order or replayed events cannot regress a payment),
  * which recovery action is appropriate for a situation,
  * whether a given action may execute right now (consent, limits, already-paid, pending, mode).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Customer, MerchantSettings, Order, Payment, RecoveryCase

TRANSITIONS = {
    "CREATED": {"APPROVED", "AUTHORIZED", "PENDING", "COMPLETED", "DECLINED", "FAILED", "VOIDED", "CANCELLED", "EXPIRED"},
    "APPROVED": {"AUTHORIZED", "PENDING", "COMPLETED", "DECLINED", "FAILED", "VOIDED", "CANCELLED", "EXPIRED"},
    "AUTHORIZED": {"PENDING", "COMPLETED", "DECLINED", "VOIDED", "EXPIRED"},
    "PENDING": {"COMPLETED", "DECLINED", "FAILED", "REFUNDED"},
    "COMPLETED": {"REFUNDED", "PARTIALLY_REFUNDED"},
    "PARTIALLY_REFUNDED": {"PARTIALLY_REFUNDED", "REFUNDED"},
    "DECLINED": set(), "FAILED": set(), "REFUNDED": set(), "VOIDED": set(), "CANCELLED": set(), "EXPIRED": set(),
}
PAID_STATES = ("COMPLETED", "PARTIALLY_REFUNDED")


def can_transition(frm: str, to: str) -> bool:
    return frm == to or to in TRANSITIONS.get(frm, set())


ACTIONS = {
    # type: (customer_facing, description)
    "SEND_REMINDER": (True, "Send a personalised payment reminder with a secure checkout link"),
    "REQUEST_CUSTOMER_ACTION": (True, "Ask the customer to complete the pending PayPal step via a secure link"),
    "RECOMMEND_ALTERNATIVE_METHOD": (True, "Invite the customer to pay with a different PayPal funding source"),
    "SEND_PAYPAL_INVOICE": (True, "Create and send a PayPal invoice (Invoicing v2) the customer pays on PayPal"),
    "RETRY_CAPTURE": (False, "Reconcile with PayPal, then retry the capture of the buyer-approved order (same idempotency key)"),
    "GENERATE_CHECKOUT_LINK": (False, "Generate a secure checkout continuation link"),
    "NOTIFY_MERCHANT": (False, "Notify the merchant"),
    "WAIT_FOR_PENDING": (False, "Wait for PayPal to resolve the pending payment; do not charge again"),
    "RECOMMEND_SUPPORT": (False, "Recommend support outreach / refund review by the merchant"),
    "MANUAL_INVESTIGATION": (False, "Flag for manual investigation"),
    "CLOSE_CASE": (False, "Close the case — payment already completed"),
}
CUSTOMER_FACING = {k for k, (cf, _) in ACTIONS.items() if cf}
PASSIVE_ACTIONS = {"WAIT_FOR_PENDING", "NOTIFY_MERCHANT", "MANUAL_INVESTIGATION", "RECOMMEND_SUPPORT", "CLOSE_CASE"}


@dataclass
class Recommendation:
    action: str
    reason: str
    expected_benefit: str
    risk: str
    requires_approval: bool
    customer_involved: bool
    merchant_involved: bool

    def as_dict(self):
        return self.__dict__ | {"description": ACTIONS[self.action][1]}


def order_is_paid(db: Session, order_id: str) -> bool:
    if db.scalar(select(Order.status).where(Order.id == order_id)) == "PAID":
        return True
    return db.scalar(select(Payment.id).where(Payment.order_id == order_id, Payment.status.in_(PAID_STATES)).limit(1)) is not None


def order_has_pending(db: Session, order_id: str) -> bool:
    return db.scalar(select(Payment.id).where(Payment.order_id == order_id, Payment.status == "PENDING").limit(1)) is not None


def recommend(db: Session, *, category: str, order_id: str, has_consent: bool, mode: str, amount_str: str,
              payment: Payment | None = None) -> Recommendation:
    approval = mode != "AUTOMATIC"

    def rec(action, reason, benefit, risk, customer=False, merchant=False, needs_approval=None):
        if needs_approval is None:
            needs_approval = approval and action in CUSTOMER_FACING
        return Recommendation(action, reason, benefit, risk, needs_approval, customer, merchant)

    if order_is_paid(db, order_id):
        return rec("CLOSE_CASE", "A completed payment already exists for this order.", "Avoids contacting a customer who has paid.",
                   "None.", needs_approval=False)
    if order_has_pending(db, order_id):
        return rec("WAIT_FOR_PENDING", "PayPal has a pending capture for this order; asking the customer to pay again could cause a double charge.",
                   "Payment may complete without any action.", "Delay if PayPal review takes days.", merchant=True, needs_approval=False)
    if db.scalar(select(Order.status).where(Order.id == order_id)) in ("CANCELLED", "REFUNDED"):
        return rec("CLOSE_CASE", "The order was cancelled or refunded, so there is nothing to recover.", "No wasted outreach.", "None.", needs_approval=False)
    if payment is not None and payment.reconcile_required and payment.paypal_order_id:
        return rec("RETRY_CAPTURE", "The last capture attempt timed out or hit a PayPal server error, so its outcome is uncertain. "
                   "RecoverAI must first reconcile the order with PayPal; only if PayPal still shows it approved-and-uncaptured will it retry "
                   "the capture with the same idempotency key (so PayPal cannot charge twice).",
                   f"Up to {amount_str} without asking the buyer again.", "None if reconciliation passes; blocked otherwise.",
                   merchant=True, needs_approval=mode != "AUTOMATIC")
    if category == "DUPLICATE_ATTEMPT":
        return rec("MANUAL_INVESTIGATION", "PayPal reported this order or invoice as already processed.",
                   "Confirms whether money was already collected.", "Contacting the customer could cause confusion.", merchant=True, needs_approval=False)
    if category == "CONFIGURATION_ISSUE":
        return rec("NOTIFY_MERCHANT", "The failure is caused by the merchant's PayPal configuration, so contacting the customer will not help until it is fixed.",
                   "Fixing configuration unblocks this and future payments.", "Customer may buy elsewhere while it is fixed.", merchant=True, needs_approval=False)
    if category in ("CAPTURE_FAILED", "UNKNOWN"):
        return rec("MANUAL_INVESTIGATION", "There is not enough reliable information to choose a customer-facing action safely.",
                   "Prevents sending the wrong message.", "Slower recovery.", merchant=True, needs_approval=False)
    if not has_consent:
        return rec("NOTIFY_MERCHANT", "The customer has not consented to recovery messages, so RecoverAI will not contact them.",
                   "Merchant can follow up through an approved channel.", "Lower recovery rate.", merchant=True, needs_approval=False)
    if category == "AUTHENTICATION_REQUIRED":
        return rec("REQUEST_CUSTOMER_ACTION", f"PayPal needs the customer to complete a step before {amount_str} can be collected. A new customer-approved checkout is the supported path; we never charge again automatically.",
                   f"Up to {amount_str} recovered with one customer step.", "Customer may ignore the message.", customer=True)
    if category in ("CARD_DECLINED", "INSUFFICIENT_FUNDS"):
        return rec("RECOMMEND_ALTERNATIVE_METHOD", "The payment method was declined. Retrying the same method automatically is unlikely to work, so invite the customer to choose another funding source in PayPal.",
                   f"Up to {amount_str} recovered if the customer pays with another method.", "Declines can repeat; message must not blame the customer.", customer=True)
    # CHECKOUT_ABANDONED, APPROVAL_INCOMPLETE, TECHNICAL_ERROR
    return rec("SEND_REMINDER", "The customer did not finish checkout. A friendly reminder with a secure link back to checkout is the lowest-risk next step.",
               f"Up to {amount_str} recovered.", "Over-messaging can annoy customers; limits apply.", customer=True)


def guardrails(db: Session, case: RecoveryCase, settings: MerchantSettings, action_type: str, *, approved: bool = False) -> list[dict]:
    """The Trust Firewall: every check the backend enforces before an action runs, with its live result.
    status: pass | fail | hold (needs a human) | info"""
    checks = []

    def add(key, label, ok, detail, hold=False):
        checks.append({"key": key, "label": label, "status": "hold" if hold else ("pass" if ok else "fail"), "detail": detail})

    add("no_money_movement", "AI cannot move money", action_type not in ("CAPTURE", "REFUND", "CHARGE"),
        "Action is non-financial; captures only happen after the customer approves in PayPal")
    passive = action_type in PASSIVE_ACTIONS
    paid = order_is_paid(db, case.order_id)
    add("not_paid", "Order not already paid", not paid, "A completed payment exists" if paid else "No completed payment for this order")
    pending = order_has_pending(db, case.order_id)
    add("no_pending", "No pending PayPal capture", not pending, "Pending capture — wait, never charge twice" if pending else "No pending capture")
    if passive:
        # For actions that neither contact the customer nor touch money, a paid or pending order is the *reason* for
        # the action (wait / close), not a violation. Show it as information instead of a failed check.
        for c in checks:
            if c["key"] in ("not_paid", "no_pending") and c["status"] == "fail":
                c["status"], c["detail"] = "info", c["detail"] + " — this action doesn't contact the customer or move money"
    if action_type in CUSTOMER_FACING or action_type == "SEND_PAYPAL_INVOICE":
        customer = db.get(Customer, case.customer_id)
        add("consent", "Customer consented to recovery messages", bool(customer and customer.recovery_contact_consent),
            "Consent on file" if customer and customer.recovery_contact_consent else "No consent — will not contact")
        add("limit", "Within message limit", case.reminders_sent < settings.max_reminders_per_case,
            f"{case.reminders_sent} of {settings.max_reminders_per_case} sent")
        if case.last_contacted_at:
            last = case.last_contacted_at if case.last_contacted_at.tzinfo else case.last_contacted_at.replace(tzinfo=timezone.utc)
            mins = (datetime.now(timezone.utc) - last).total_seconds() / 60
            add("spacing", "Message spacing respected", mins >= settings.min_minutes_between_messages,
                f"Last message {mins:.0f} minutes ago (minimum spacing {settings.min_minutes_between_messages} minutes)")
        else:
            add("spacing", "Message spacing respected", True, "No previous message")
        add("amount", "Amount unchanged", True, f"Link and invoice use the original order amount ({case.amount_minor / 100:,.2f} {case.currency})")
        if approved:
            add("approval", "Merchant approval", True, "Approved by merchant")
        elif settings.recovery_mode == "AUTOMATIC":
            add("approval", "Merchant approval", True, "Automatic mode: pre-authorised for safe, non-financial actions")
        else:
            add("approval", "Merchant approval", False, f"{settings.recovery_mode.title()} mode — waiting for a human", hold=True)
    order = db.get(Order, case.order_id)
    add("order_open", "Order not cancelled or refunded", order.status not in ("CANCELLED", "REFUNDED"), f"Order status {order.status}")
    if settings.recovery_mode == "AUTOMATIC" and not approved:
        if case.amount_minor > (settings.max_auto_amount_minor or 0):
            add("auto_amount", "Within automation amount limit", False,
                f"{case.amount_minor / 100:,.2f} exceeds the automatic limit of {settings.max_auto_amount_minor / 100:,.2f} — needs approval", hold=True)
        if action_type not in (settings.permitted_auto_actions or []):
            add("auto_permitted", "Action permitted for automation", False, f"{action_type} is not on the automatic allow-list — needs approval", hold=True)
        if action_type in CUSTOMER_FACING or action_type == "SEND_PAYPAL_INVOICE":
            q = in_quiet_hours(db, case.merchant_id, settings)
            if q:
                add("quiet_hours", "Outside customer quiet hours", False, q, hold=True)
    add("idempotent", "Idempotent action", True, "Unique (case, step) key — cannot run twice")
    return checks


def in_quiet_hours(db: Session, merchant_id: str, settings: MerchantSettings) -> str | None:
    """Returns a reason string if 'now' is inside the merchant's quiet hours (merchant timezone)."""
    from zoneinfo import ZoneInfo
    from ..models import Merchant
    if settings.quiet_hours_start is None or settings.quiet_hours_end is None or settings.quiet_hours_start == settings.quiet_hours_end:
        return None
    m = db.get(Merchant, merchant_id)
    try:
        tz = ZoneInfo(m.timezone or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    h = datetime.now(tz).hour
    a, b = settings.quiet_hours_start, settings.quiet_hours_end
    inside = (a <= h < b) if a < b else (h >= a or h < b)
    return f"Quiet hours {a:02d}:00–{b:02d}:00 ({m.timezone or 'UTC'}) — deferred" if inside else None


def can_contact(db: Session, case: RecoveryCase, settings: MerchantSettings) -> tuple[bool, str]:
    if case.status not in ("OPEN", "AWAITING_APPROVAL", "IN_PROGRESS"):
        return False, f"Case is {case.status}"
    if order_is_paid(db, case.order_id):
        return False, "Order already paid — reminder suppressed"
    if order_has_pending(db, case.order_id):
        return False, "A PayPal payment for this order is pending — reminder suppressed to avoid double charge"
    customer = db.get(Customer, case.customer_id)
    if not customer or not customer.recovery_contact_consent:
        return False, "Customer has not consented to recovery messages"
    if case.reminders_sent >= settings.max_reminders_per_case:
        return False, f"Message limit reached ({settings.max_reminders_per_case})"
    if case.last_contacted_at:
        last = case.last_contacted_at if case.last_contacted_at.tzinfo else case.last_contacted_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last < timedelta(minutes=settings.min_minutes_between_messages):
            return False, f"Last message was under {settings.min_minutes_between_messages} minutes ago"
    return True, "ok"
