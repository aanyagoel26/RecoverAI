"""Customer communication: message generation (LLM with template fallback) and delivery.

Delivery uses Resend when RESEND_API_KEY is configured. Otherwise messages are stored as PREVIEW_ONLY and the
UI says plainly that nothing was sent.
"""
import html
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from ..models import CommunicationLog, Customer, Order, RecoveryCase
from . import llm
from .audit import audit, money

TONES = ("professional", "friendly", "urgent")

_OPENERS = {
    "professional": "We noticed that your recent payment of {amount} for {item} was not completed.",
    "friendly": "Looks like your payment of {amount} for {item} didn't quite go through — no worries, it happens!",
    "urgent": "Your payment of {amount} for {item} is still incomplete, and we'd hate for you to miss out.",
}
_CATEGORY_LINES = {
    "CARD_DECLINED": "Your payment method wasn't accepted this time. You can securely complete your purchase with PayPal and choose a different funding source if you like.",
    "INSUFFICIENT_FUNDS": "You can securely complete your purchase with PayPal and choose whichever funding source works best for you.",
    "AUTHENTICATION_REQUIRED": "PayPal needs one quick confirmation step from you to finish the payment.",
    "CHECKOUT_ABANDONED": "Your order is still saved and ready whenever you are.",
    "APPROVAL_INCOMPLETE": "Your order is still saved — you just need to confirm the payment in PayPal.",
    "TECHNICAL_ERROR": "A temporary technical issue interrupted checkout. Your order is still available.",
}
_SUBJECTS = {
    "professional": "Action needed: complete your payment for order {order}",
    "friendly": "Your order is almost complete!",
    "urgent": "Reminder: your order {order} is waiting for payment",
}


def template_message(*, tone: str, customer_name: str, amount: str, item: str, order_number: str, category: str,
                     link: str | None, brand: str, support_email: str | None) -> tuple[str, str]:
    first = customer_name.split()[0] if customer_name else "there"
    lines = [f"Hi {first},", "", _OPENERS[tone].format(amount=amount, item=item),
             _CATEGORY_LINES.get(category, "Your order is still available.")]
    if link:
        lines += ["", f"Continue securely: {link}"]
    lines += ["", "If you ran into any difficulty, just reply to this email — we're happy to help."
              + (f" You can also reach us at {support_email}." if support_email else ""),
              "", "Thank you for shopping with us,", brand]
    if tone == "urgent":
        lines.insert(-3, "Your saved order will remain available for a limited time.")
    return _SUBJECTS[tone].format(order=order_number), "\n".join(lines)


def generate(db: Session, case: RecoveryCase, *, tone: str, link: str | None, brand: str,
             support_email: str | None) -> dict:
    if tone not in TONES:
        tone = "friendly"
    order: Order = db.get(Order, case.order_id)
    cust: Customer = db.get(Customer, case.customer_id)
    amount = money(case.amount_minor, case.currency)
    subject, body = template_message(tone=tone, customer_name=cust.name, amount=amount, item=order.description,
                                     order_number=order.order_number, category=case.failure_category or "",
                                     link=link, brand=brand, support_email=support_email)
    generated_by = "rules"
    try:
        out = llm.complete_json(
            "Rewrite this payment-recovery email in a " + tone + " tone. Keep every fact exactly (amount, order, link). "
            "Do not promise the payment will succeed, do not mention card details, do not blame the customer, "
            "and do not invent discounts. Keep it under 140 words.\n\n"
            f"SUBJECT: {subject}\n\nBODY:\n{body}\n\nReturn JSON {{\"subject\": str, \"body\": str}}",
            system="You write concise, warm, compliant transactional emails for an online store.",
            db=db, merchant_id=case.merchant_id, purpose="message")
        cand_subject, cand_body = str(out.get("subject", ""))[:200], str(out.get("body", ""))
        # Guardrails: the LLM output must keep the link and the exact amount, otherwise use the template.
        if cand_body and amount in cand_body and (not link or link in cand_body):
            subject, body, generated_by = cand_subject or subject, cand_body, "llm"
    except llm.LLMUnavailable:
        pass
    return {"subject": subject, "body": body, "tone": tone, "generated_by": generated_by, "recovery_link": link}


def deliver(db: Session, log_row: CommunicationLog, to_email: str, *, test: bool = False) -> CommunicationLog:
    """Email via the merchant's Resend key (dashboard) or RESEND_API_KEY. SENT means *accepted by the provider*;
    nothing is ever reported as delivered without provider confirmation."""
    from . import providers
    cfg = providers.email_config(db, log_row.merchant_id)
    log_row.to_address = to_email
    if not cfg:
        log_row.delivery_status = "PREVIEW_ONLY"
        log_row.provider = None
        log_row.error = "No email provider configured (Integrations → Resend). Message recorded but NOT sent."
        return log_row
    try:
        r = httpx.post("https://api.resend.com/emails", timeout=15, headers={"Authorization": f"Bearer {cfg['api_key']}"},
                       json={"from": cfg["from_email"], "to": [to_email], "subject": log_row.subject,
                             "html": "<p>" + html.escape(log_row.body).replace("\n", "<br>") + "</p>", "text": log_row.body})
        if r.status_code < 300:
            log_row.delivery_status = "TEST_SENT" if test else "SENT"
            log_row.provider, log_row.provider_message_id = "resend", r.json().get("id")
            log_row.sent_at = datetime.now(timezone.utc)
        else:
            log_row.delivery_status, log_row.error = "FAILED", f"Resend HTTP {r.status_code}: {r.text[:200]}"
    except httpx.HTTPError as e:
        log_row.delivery_status, log_row.error = "FAILED", f"Resend unreachable: {type(e).__name__}"
    audit(db, log_row.merchant_id, "system", "email.delivery", f"Email to customer: {log_row.delivery_status}",
          entity_type="communication", entity_id=log_row.id)
    return log_row


def sms_text(*, first_name: str, brand: str, order_number: str, amount: str, link: str) -> str:
    return f"{brand}: Hi {first_name}, your order {order_number} ({amount}) isn't complete yet. Finish securely with PayPal: {link} Reply STOP to opt out."


def deliver_sms(db: Session, log_row: CommunicationLog, to_number: str, *, test: bool = False) -> CommunicationLog:
    """SMS via Twilio Messages API. Delivery is confirmed only by Twilio's signed status callback."""
    from ..config import get_settings
    from . import providers
    cfg = providers.sms_config(db, log_row.merchant_id)
    log_row.to_address = to_number
    if not cfg:
        log_row.delivery_status, log_row.error = "PREVIEW_ONLY", "No SMS provider configured (Integrations → Twilio). Message recorded but NOT sent."
        return log_row
    callback = f"{get_settings().public_api_url.rstrip('/')}/api/webhooks/twilio/{log_row.merchant_id}"
    try:
        r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{cfg['account_sid']}/Messages.json", timeout=15,
                       auth=(cfg["account_sid"], cfg["auth_token"]),
                       data={"To": to_number, "From": cfg["from_number"], "Body": log_row.body, "StatusCallback": callback})
        if r.status_code < 300:
            log_row.delivery_status = "TEST_SENT" if test else "SENT"
            log_row.provider, log_row.provider_message_id = "twilio", r.json().get("sid")
            log_row.sent_at = datetime.now(timezone.utc)
        else:
            log_row.delivery_status, log_row.error = "FAILED", f"Twilio HTTP {r.status_code}: {r.text[:200]}"
    except httpx.HTTPError as e:
        log_row.delivery_status, log_row.error = "FAILED", f"Twilio unreachable: {type(e).__name__}"
    audit(db, log_row.merchant_id, "system", "sms.delivery", f"SMS to customer: {log_row.delivery_status}", entity_type="communication", entity_id=log_row.id)
    return log_row


def twilio_signature_valid(auth_token: str, url: str, params: dict, signature: str) -> bool:
    import base64
    import hashlib
    import hmac
    payload = url + "".join(k + params[k] for k in sorted(params))
    expected = base64.b64encode(hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, signature or "")
