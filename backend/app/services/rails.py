"""Recovery rails: which PayPal product gives this customer the best chance to complete the payment.

Deterministic routing (the LLM only explains it). Every rail is a customer-approved PayPal flow; none re-charges
an instrument and none bypasses PayPal approval. Eligibility for Pay Later / Venmo is always decided by PayPal at
checkout — RecoverAI only chooses what to *offer first*.
"""
from dataclasses import asdict, dataclass

RAIL_INFO = {
    "PAYPAL_CHECKOUT": ("PayPal Checkout", "Secure PayPal checkout link (wallet, card or any eligible funding source)"),
    "PAY_LATER": ("PayPal Pay Later", "Checkout link that leads with PayPal Pay Later (e.g. Pay in 4) plus Pay Later messaging, if the buyer is eligible"),
    "VENMO": ("Venmo", "Checkout link with Venmo enabled for fast mobile completion (US buyers, if eligible)"),
    "PAYPAL_INVOICE": ("PayPal Invoice", "PayPal Invoicing v2 invoice the customer can pay on PayPal (wallet, Pay Later or card as guest)"),
}


@dataclass
class Rail:
    key: str
    label: str
    product: str
    reason: str
    alternatives: list

    def as_dict(self):
        return asdict(self)


def choose_rail(*, category: str | None, amount_minor: int, country: str | None, attempts: int, case_type: str) -> Rail:
    amount = amount_minor / 100
    alts = []

    def mk(key, reason):
        label, product = RAIL_INFO[key]
        return Rail(key, label, product, reason, [a for a in alts if a["key"] != key])

    alts = [{"key": k, "label": v[0]} for k, v in RAIL_INFO.items()]
    if amount >= 1000 or attempts >= 3:
        return mk("PAYPAL_INVOICE", f"High-value order ({amount:,.0f}) or repeated failed attempts ({attempts}): a PayPal invoice gives the buyer a "
                  "durable payment request they can pay later from any device, instead of another checkout attempt.")
    if category in ("INSUFFICIENT_FUNDS",) or (category == "CARD_DECLINED" and amount >= 30):
        return mk("PAY_LATER", "The funding source was declined. Leading with PayPal Pay Later lets an eligible buyer split the cost "
                  "instead of retrying the same instrument — addressing the likely affordability problem.")
    if (country or "").upper() == "US" and amount < 300 and case_type in ("ABANDONED_CHECKOUT", "APPROVAL_INCOMPLETE"):
        return mk("VENMO", "US buyer with a small basket who left mid-checkout: enabling Venmo gives a one-tap mobile completion path.")
    return mk("PAYPAL_CHECKOUT", "A fresh customer-approved PayPal checkout is the simplest safe path back to a completed payment.")
