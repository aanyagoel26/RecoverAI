"""Failure Lab: produce *real* PayPal Sandbox failures on demand, so the agent reacts to genuine PayPal responses.

Uses PayPal's documented sandbox negative testing (`PayPal-Mock-Response: {"mock_application_codes": "<ISSUE>"}`) on
`POST /v2/checkout/orders/{id}/capture`. Whatever PayPal returns is recorded verbatim — if PayPal answers with a different
error than requested, that real answer is what the agent analyses. Payments are labelled PAYPAL_SANDBOX with
`negative_testing` in their raw response. Without PayPal credentials the lab falls back to clearly labelled DEMO data.
"""
import secrets
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import CheckoutSession, Customer, Order, Payment, Product
from . import agent, checkout
from .audit import audit
from . import providers
from .paypal_client import PayPalError

SCENARIOS = {
    "card_declined": {"label": "Funding source declined", "issue": "INSTRUMENT_DECLINED", "amount_minor": 24900,
                      "item": "Trailblazer 45L Backpack", "expect_rail": "PAY_LATER",
                      "story": "Buyer's card is declined at capture. Agent should lead with PayPal Pay Later, not retry the card."},
    "payer_action": {"label": "Buyer action required", "issue": "PAYER_ACTION_REQUIRED", "amount_minor": 32900,
                     "item": "Summit Down Jacket", "expect_rail": "PAYPAL_CHECKOUT",
                     "story": "PayPal needs the buyer to complete a step. Agent requests a fresh customer-approved checkout."},
    "high_value": {"label": "High-value decline", "issue": "INSTRUMENT_DECLINED", "amount_minor": 150000,
                   "item": "Expedition Bundle", "expect_rail": "PAYPAL_INVOICE",
                   "story": "A $1,500 order fails. Agent routes to a PayPal Invoice the buyer can pay on PayPal."},
    "refused": {"label": "Transaction refused", "issue": "TRANSACTION_REFUSED", "amount_minor": 8900,
                "item": "Ridge Trekking Poles", "expect_rail": "PAYPAL_CHECKOUT",
                "story": "PayPal refuses the transaction. Agent explains and offers a new checkout."},
    "timeout": {"label": "Capture timeout (unsafe retry)", "issue": "INTERNAL_SERVER_ERROR", "amount_minor": 21900,
                "item": "Granite Hiking Boots", "expect_rail": "PAYPAL_CHECKOUT",
                "story": "PayPal answers the capture with a server error, so the outcome is uncertain. The agent must reconcile before any retry — "
                         "and the Trust Firewall blocks the retry because the buyer never approved the order."},
    "abandoned": {"label": "Abandoned checkout", "issue": None, "amount_minor": 12900, "item": "Merino Base Layer Set",
                  "expect_rail": "VENMO", "story": "A real PayPal order is created but never approved. Agent opens an abandoned-checkout case."},
}


def _uncertain(db: Session, payment: Payment, issue: str, real: bool):
    """Timeout / 5xx: outcome unknown. Do NOT mark failed — flag for reconciliation and let the agent plan a safe retry."""
    payment.paypal_issue, payment.reconcile_required = issue, True
    db.add(agent.PaymentEvent(merchant_id=payment.merchant_id, payment_id=payment.id, event_type="capture.uncertain", source="PAYPAL_API" if real else "DEMO_SIMULATOR",
                              from_status=payment.status, to_status=payment.status, detail={"issue": issue, "note": "outcome unknown — reconcile before retry"}))
    case = agent.ensure_case(db, payment=payment, case_type="APPROVAL_INCOMPLETE")
    agent.analyse_case(db, case, payment=payment)
    agent.plan_actions(db, case, trigger=f"{'PayPal API response' if real else 'Demo simulator'}: capture of {payment.reference} returned {issue} — outcome uncertain")


def run(db: Session, merchant_id: str, scenario: str, *, customer_name: str | None, customer_email: str | None,
        country: str = "US", actor_id: str | None = None) -> dict:
    if scenario not in SCENARIOS:
        raise HTTPException(400, "Unknown scenario")
    sc = SCENARIOS[scenario]
    s = get_settings()
    real = bool(providers.paypal_config(db, merchant_id, "sandbox")) and providers.paypal_environment(db, merchant_id) == "sandbox"
    source = "PAYPAL_SANDBOX" if real else "DEMO_SIMULATED"
    name = (customer_name or "Jordan Rivera").strip()[:200]
    email = (customer_email or f"lab.{secrets.token_hex(3)}@example.com").strip().lower()[:320]
    cust = db.scalar(select(Customer).where(Customer.merchant_id == merchant_id, Customer.email == email))
    if not cust:
        cust = Customer(merchant_id=merchant_id, name=name, email=email, country=country[:2].upper(), recovery_contact_consent=True)
        db.add(cust)
        db.flush()
    product = db.scalar(select(Product).where(Product.merchant_id == merchant_id, Product.name == sc["item"]))
    order = Order(merchant_id=merchant_id, customer_id=cust.id, product_id=product.id if product else None,
                  order_number=f"LAB-{secrets.token_hex(3).upper()}", description=sc["item"], amount_minor=sc["amount_minor"],
                  data_source=source)
    db.add(order)
    db.flush()
    sess = CheckoutSession(merchant_id=merchant_id, order_id=order.id, customer_id=cust.id, status="ACTIVE", data_source=source)
    db.add(sess)
    db.flush()
    agent.session_link(sess)
    audit(db, merchant_id, "user", "lab.scenario", f"Failure Lab: {sc['label']} for {order.order_number} ({'real PayPal Sandbox' if real else 'DEMO simulation'})",
          actor_id=actor_id, entity_type="order", entity_id=order.id)

    paypal_evidence = None
    if real:
        created = checkout.create_paypal_order(db, order, sess)  # real POST /v2/checkout/orders
        payment = db.get(Payment, created["payment_id"])
        if sc["issue"]:
            try:
                providers.paypal_for(db, merchant_id, "sandbox").capture_order(payment.paypal_order_id, request_id=f"lab-{payment.id}", mock_issue=sc["issue"])
                # PayPal accepted the capture despite the mock header: record the truth, don't pretend it failed
                checkout.sync_from_paypal(db, payment)
                paypal_evidence = {"note": "PayPal did not return the requested negative-test error; synced real status instead"}
            except PayPalError as e:
                issue = e.issue or e.name
                payment.paypal_debug_id = e.debug_id
                payment.raw_response = (payment.raw_response or {}) | {"error": e.raw, "debug_id": e.debug_id,
                                                                       "negative_testing": {"requested": sc["issue"], "returned": issue}}
                paypal_evidence = {"http_status": e.http_status, "issue": issue, "debug_id": e.debug_id, "requested": sc["issue"],
                                   "message": e.message}
                sess.status = "EXPIRED"
                if e.http_status == 0 or e.http_status >= 500:
                    _uncertain(db, payment, issue, real=True)
                else:
                    agent.apply_status(db, payment, "DECLINED", source="PAYPAL_API", issue=issue,
                                       detail={"negative_testing": True, "debug_id": e.debug_id, "http": e.http_status})
        else:
            paypal_evidence = {"paypal_order_id": payment.paypal_order_id, "status": "CREATED (never approved)"}
    else:
        payment = Payment(merchant_id=merchant_id, order_id=order.id, customer_id=cust.id, checkout_session_id=sess.id,
                          reference=agent.new_reference(), amount_minor=order.amount_minor, currency=order.currency,
                          status="CREATED", data_source=source, paypal_order_id=f"DEMO-{uuid.uuid4().hex[:17].upper()}")
        db.add(payment)
        db.flush()
        db.add(agent.PaymentEvent(merchant_id=merchant_id, payment_id=payment.id, event_type="status.changed", to_status="CREATED",
                                  source="DEMO_SIMULATOR", detail={"simulated": True}))
        if sc["issue"] == "INTERNAL_SERVER_ERROR":
            payment.raw_response = {"simulated": True, "issue": sc["issue"], "simulated_paypal_status": "PAYER_ACTION_REQUIRED"}
            sess.status = "EXPIRED"
            _uncertain(db, payment, sc["issue"], real=False)
        elif sc["issue"]:
            payment.raw_response = {"simulated": True, "issue": sc["issue"]}
            sess.status = "EXPIRED"
            agent.apply_status(db, payment, "DECLINED", source="DEMO_SIMULATOR", issue=sc["issue"], detail={"simulated": True})
        paypal_evidence = {"simulated": True, "issue": sc["issue"], "note": "PayPal not configured — DEMO simulation"}

    if not sc["issue"]:  # abandoned: the buyer walked away from a real PayPal order
        sess.status, sess.abandoned_at = "ABANDONED", agent.utcnow()
        case = agent.ensure_case(db, session=sess)
        agent.analyse_case(db, case)
        agent.plan_actions(db, case, trigger=f"{'PayPal Sandbox' if real else 'Demo'}: checkout {order.order_number} abandoned — PayPal order never approved")
    case = db.scalar(select(agent.RecoveryCase).where(agent.RecoveryCase.order_id == order.id))
    run_row = db.scalar(select(agent.AgentRun).where(agent.AgentRun.case_id == case.id).order_by(agent.AgentRun.created_at.desc()).limit(1)) if case else None
    db.commit()
    return {"scenario": scenario, "label": sc["label"], "story": sc["story"], "data_source": source, "order_number": order.order_number,
            "payment_id": payment.id, "case_id": case.id if case else None, "run_id": run_row.id if run_row else None,
            "paypal": paypal_evidence}
