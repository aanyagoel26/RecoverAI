"""Deterministic failure classification from PayPal evidence.

Only documented PayPal signals are mapped. Anything we cannot attribute confidently becomes UNKNOWN with an
explanation of what evidence was missing. The LLM never decides the category; it only explains it.
"""
from dataclasses import dataclass, field

# PayPal Orders v2 issue codes -> category. Codes come from PayPal's documented Orders v2 errors.
ISSUE_MAP = {
    "INSUFFICIENT_FUNDS": "INSUFFICIENT_FUNDS",
    "INSTRUMENT_DECLINED": "CARD_DECLINED",
    "TRANSACTION_REFUSED": "CARD_DECLINED",
    "CARD_EXPIRED": "CARD_DECLINED",
    "PAYER_ACTION_REQUIRED": "AUTHENTICATION_REQUIRED",
    "PAYER_CANNOT_PAY": "CARD_DECLINED",
    "ORDER_NOT_APPROVED": "APPROVAL_INCOMPLETE",
    "ORDER_EXPIRED": "APPROVAL_INCOMPLETE",
    "ORDER_ALREADY_CAPTURED": "DUPLICATE_ATTEMPT",
    "DUPLICATE_INVOICE_ID": "DUPLICATE_ATTEMPT",
    "DUPLICATE_REQUEST_ID": "DUPLICATE_ATTEMPT",
    "CURRENCY_NOT_SUPPORTED_FOR_RECEIVER": "CONFIGURATION_ISSUE",
    "CURRENCY_NOT_SUPPORTED_FOR_COUNTRY": "CONFIGURATION_ISSUE",
    "PAYEE_ACCOUNT_RESTRICTED": "CONFIGURATION_ISSUE",
    "PAYEE_ACCOUNT_LOCKED_OR_CLOSED": "CONFIGURATION_ISSUE",
    "PAYEE_NOT_ENABLED_FOR_CARD_PROCESSING": "CONFIGURATION_ISSUE",
    "PERMISSION_DENIED": "CONFIGURATION_ISSUE",
    "AUTHENTICATION_FAILURE": "CONFIGURATION_ISSUE",
    "INVALID_CLIENT": "CONFIGURATION_ISSUE",
    "INTERNAL_SERVER_ERROR": "TECHNICAL_ERROR",
    "INTERNAL_SERVICE_ERROR": "TECHNICAL_ERROR",
    "NETWORK_ERROR": "TECHNICAL_ERROR",
    "SERVICE_UNAVAILABLE": "TECHNICAL_ERROR",
    "CHECKOUT_ABANDONED": "CHECKOUT_ABANDONED",
}

CATEGORY_INFO = {
    "INSUFFICIENT_FUNDS": dict(label="Insufficient funds", severity=55, customer=True, merchant=False,
        explain="PayPal reported that the payer's funding source did not have enough available funds."),
    "CARD_DECLINED": dict(label="Card / instrument declined", severity=60, customer=True, merchant=False,
        explain="PayPal reported that the payer's chosen payment method was declined."),
    "AUTHENTICATION_REQUIRED": dict(label="Customer action required", severity=45, customer=True, merchant=False,
        explain="PayPal indicated that the payer must complete an additional step (such as authentication or re-approval) before the payment can be captured."),
    "CHECKOUT_ABANDONED": dict(label="Checkout abandoned", severity=35, customer=True, merchant=False,
        explain="The customer started checkout but did not finish. No PayPal payment failure occurred."),
    "APPROVAL_INCOMPLETE": dict(label="Approval incomplete", severity=40, customer=True, merchant=False,
        explain="A PayPal order exists but the payer had not approved it, so it could not be captured."),
    "CAPTURE_FAILED": dict(label="Capture failed", severity=70, customer=False, merchant=True,
        explain="The payer approved the order but the capture did not complete."),
    "TECHNICAL_ERROR": dict(label="Network / technical error", severity=50, customer=False, merchant=True,
        explain="The request to PayPal failed for a technical reason, not because of the payer."),
    "CONFIGURATION_ISSUE": dict(label="Currency / configuration issue", severity=80, customer=False, merchant=True,
        explain="PayPal rejected the payment because of a merchant account or currency configuration problem."),
    "DUPLICATE_ATTEMPT": dict(label="Duplicate payment attempt", severity=30, customer=False, merchant=False,
        explain="PayPal reported that this order or invoice was already processed. This is a duplicate, not a lost payment."),
    "UNKNOWN": dict(label="Unknown / unclassified", severity=50, customer=False, merchant=True,
        explain="The PayPal response does not contain enough information to determine why the payment did not complete."),
}


@dataclass
class Classification:
    category: str
    basis: str
    evidence: list = field(default_factory=list)

    @property
    def info(self):
        return CATEGORY_INFO[self.category]


def classify(payment_status: str, issue: str | None, raw: dict | None, has_session_only: bool = False) -> Classification:
    evidence = []
    raw = raw or {}
    if issue:
        evidence.append(f"PayPal issue/reason code: {issue}")
    if raw.get("debug_id"):
        evidence.append(f"PayPal debug_id: {raw['debug_id']}")
    capture = raw.get("capture") or {}
    if capture.get("status"):
        evidence.append(f"Capture status: {capture['status']}")
    pr = capture.get("processor_response") or {}
    if pr.get("response_code"):
        evidence.append(f"Processor response_code: {pr['response_code']} (raw, not interpreted)")

    if has_session_only:
        return Classification("CHECKOUT_ABANDONED", "Checkout session inactive with no completed payment", evidence)

    if issue:
        key = issue.upper()
        for code, cat in ISSUE_MAP.items():
            if code in key:
                return Classification(cat, f"Mapped from PayPal code '{issue}'", evidence)

    if payment_status == "APPROVED":
        return Classification("CAPTURE_FAILED", "Order approved by payer but not captured", evidence)
    if payment_status == "CREATED":
        return Classification("APPROVAL_INCOMPLETE", "PayPal order created but never approved by the payer", evidence)
    if payment_status == "DECLINED" and capture.get("status") == "DECLINED":
        return Classification("CAPTURE_FAILED", "Capture returned status DECLINED without an issue code", evidence)

    return Classification("UNKNOWN", "No documented PayPal signal available to attribute a cause", evidence)
