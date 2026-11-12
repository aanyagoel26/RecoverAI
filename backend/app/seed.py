"""Demo-mode seed data. Every row is labelled DEMO_SIMULATED and the demo merchant has is_demo=True.

Payments: 50 attempts = 12 completed, 15 failed (declined/failed), 8 pending, 10 created (never approved), 5 approved-not-captured.
Orders: 46 (incl. 10 abandoned checkouts). Customers: 20. Historical recovery cases: 4 closed (3 recovered).
"""
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import (AgentRun, PayPalInvoice, AIAnalysis, AuditLog, CheckoutSession, CommunicationLog, Customer, Merchant, MerchantAnalytics,
                     MerchantSettings, Notification, Order, Payment, PaymentEvent, PayPalConnection, Product,
                     RecoveryAction, RecoveryCase, RevenuePrediction, User, WebhookEvent)
from .security import hash_password
from .services import agent

DEMO_EMAIL = "demo@recoverai.dev"
DEMO_PASSWORD = "RecoverAI-demo-2026"

CUSTOMERS = [
    ("Alex Morgan", "US"), ("Priya Sharma", "IN"), ("Jordan Lee", "US"), ("Sofia Rossi", "IT"), ("Liam O'Connor", "IE"),
    ("Mei Chen", "SG"), ("Noah Williams", "US"), ("Aisha Khan", "GB"), ("Lucas Martin", "FR"), ("Emma Schmidt", "DE"),
    ("Diego Alvarez", "ES"), ("Hana Sato", "JP"), ("Oliver Brown", "AU"), ("Zara Ahmed", "AE"), ("Ethan Clarke", "CA"),
    ("Isabella Costa", "BR"), ("Rohan Mehta", "IN"), ("Chloe Dubois", "FR"), ("Samuel Okafor", "NG"), ("Grace Kim", "US"),
]
PRODUCTS = [
    ("Trailblazer 45L Backpack", "TB-45", 24900), ("Summit Down Jacket", "SDJ-01", 32900), ("Ridge Trekking Poles", "RTP-2", 8900),
    ("Alpine 2P Tent", "A2P-T", 45900), ("Merino Base Layer Set", "MBL-S", 12900), ("Granite Hiking Boots", "GHB-9", 21900),
    ("Expedition Bundle", "EXP-B", 150000), ("Headlamp Pro 600", "HLP-6", 5900),
]
FAILED_ISSUES = [  # (status, issue)
    ("DECLINED", "INSTRUMENT_DECLINED"), ("DECLINED", "INSTRUMENT_DECLINED"), ("DECLINED", "INSTRUMENT_DECLINED"),
    ("DECLINED", "PAYER_ACTION_REQUIRED"), ("DECLINED", "PAYER_ACTION_REQUIRED"), ("DECLINED", "INSUFFICIENT_FUNDS"),
    ("FAILED", "DUPLICATE_INVOICE_ID"), ("FAILED", "CURRENCY_NOT_SUPPORTED_FOR_RECEIVER"), ("FAILED", "INTERNAL_SERVER_ERROR"),
    ("FAILED", None), ("DECLINED", "INSTRUMENT_DECLINED"),
]
PENDING_REASONS = ["PENDING_REVIEW", "ECHECK", "PENDING_REVIEW", "UNILATERAL", "PENDING_REVIEW", "ECHECK", "PENDING_REVIEW", "OTHER"]

TENANT_TABLES = [AgentRun, PayPalInvoice, AIAnalysis, CommunicationLog, RecoveryAction, RevenuePrediction, MerchantAnalytics, Notification, AuditLog,
                 PaymentEvent, RecoveryCase, Payment, CheckoutSession, Order, Product, Customer, PayPalConnection, MerchantSettings]


def wipe_merchant_data(db: Session, mid: str):
    db.execute(delete(WebhookEvent).where(WebhookEvent.merchant_id == mid))
    for t in TENANT_TABLES:
        db.execute(delete(t).where(t.merchant_id == mid))
    db.flush()


def ensure_demo(db: Session) -> Merchant:
    user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
    if user:
        return db.get(Merchant, user.merchant_id)
    m = Merchant(name="Northwind Outfitters", website="https://northwind-outfitters.example", industry="Outdoor gear",
                 is_demo=True, onboarding_complete=True)
    db.add(m)
    db.flush()
    db.add(User(merchant_id=m.id, email=DEMO_EMAIL, full_name="Maya Patel", password_hash=hash_password(DEMO_PASSWORD), role="owner"))
    seed_demo_data(db, m)
    db.commit()
    return m


def seed_demo_data(db: Session, m: Merchant, rng_seed: int = 42):
    rng = random.Random(rng_seed)
    now = datetime.now(timezone.utc)
    mid = m.id
    db.add(MerchantSettings(merchant_id=mid, recovery_mode="APPROVAL", brand_name="Northwind Outfitters",
                            support_email="help@northwind-outfitters.example", default_tone="friendly"))
    db.add(PayPalConnection(merchant_id=mid))
    customers = []
    for i, (name, country) in enumerate(CUSTOMERS):
        email = name.lower().replace(" ", ".").replace("'", "") + "@example.com"
        c = Customer(merchant_id=mid, name=name, email=email, country=country, is_demo=True,
                     recovery_contact_consent=i not in (13, 18), marketing_consent=i % 3 == 0)
        db.add(c)
        customers.append(c)
    products = []
    for name, sku, price in PRODUCTS:
        p = Product(merchant_id=mid, name=name, sku=sku, price_minor=price)
        db.add(p)
        products.append(p)
    db.flush()

    counter = {"order": 1040}

    def mk_order(cust, prod, at, status="OPEN", qty=1):
        counter["order"] += 1
        o = Order(merchant_id=mid, customer_id=cust.id, product_id=prod.id, order_number=f"NW-{counter['order']}",
                  description=prod.name + (f" ×{qty}" if qty > 1 else ""), amount_minor=prod.price_minor * qty, status=status,
                  data_source="DEMO_SIMULATED", created_at=at, updated_at=at, paid_at=at if status == "PAID" else None)
        db.add(o)
        db.flush()
        return o

    def mk_session(o, at, status, created=False, approved=False, captured=False):
        s = CheckoutSession(merchant_id=mid, order_id=o.id, customer_id=o.customer_id, status=status, last_activity_at=at,
                            paypal_order_created=created, paypal_order_approved=approved, paypal_order_captured=captured,
                            abandoned_at=at + timedelta(minutes=35) if status == "ABANDONED" else None,
                            data_source="DEMO_SIMULATED", created_at=at, updated_at=at)
        db.add(s)
        db.flush()
        return s

    def mk_pay(o, at, status, issue=None, ref=None, session=None, method="paypal"):
        p = Payment(merchant_id=mid, order_id=o.id, customer_id=o.customer_id, checkout_session_id=session.id if session else None,
                    reference=ref or agent.new_reference(), amount_minor=o.amount_minor, currency=o.currency, status=status,
                    data_source="DEMO_SIMULATED", paypal_order_id=f"DEMO-{rng.getrandbits(64):016X}", payment_method=method,
                    paypal_issue=issue, status_verified_by="DEMO_SIMULATOR", created_at=at, updated_at=at + timedelta(minutes=2),
                    completed_at=at + timedelta(minutes=2) if status == "COMPLETED" else None)
        if status == "COMPLETED":
            p.paypal_capture_id = f"DEMOCAP-{rng.getrandbits(48):012X}"
        if issue or status in ("DECLINED", "FAILED"):
            p.raw_response = {"simulated": True, "issue": issue, "debug_id": f"demo{rng.getrandbits(32):08x}" if issue else None}
        if status == "PENDING":
            p.raw_response = {"simulated": True, "capture": {"status": "PENDING", "status_details": {"reason": issue}}}
        db.add(p)
        db.flush()
        db.add(PaymentEvent(merchant_id=mid, payment_id=p.id, event_type="status.changed", from_status=None, to_status="CREATED",
                            source="DEMO_SIMULATOR", created_at=at, detail={"simulated": True}))
        if status != "CREATED":
            db.add(PaymentEvent(merchant_id=mid, payment_id=p.id, event_type="status.changed", from_status="CREATED", to_status=status,
                                source="DEMO_SIMULATOR", created_at=at + timedelta(minutes=2), detail={"simulated": True, "issue": issue}))
        return p

    def ago(days=0, hours=0, minutes=0):
        return now - timedelta(days=days, hours=hours, minutes=minutes)

    pick = lambda: rng.choice(customers[1:])  # noqa: E731  (Alex reserved for hero case)
    prod = lambda: rng.choice(products[:6] + [products[7]])  # noqa: E731

    # a) 7 single completed orders
    for _ in range(7):
        at = ago(days=rng.randint(1, 28), hours=rng.randint(0, 23))
        o = mk_order(pick(), prod(), at, "PAID")
        s = mk_session(o, at, "CONVERTED", True, True, True)
        mk_pay(o, at, "COMPLETED", session=s)
    # b) 2 orders: created (never approved) then completed
    for _ in range(2):
        at = ago(days=rng.randint(3, 20))
        o = mk_order(pick(), prod(), at, "PAID")
        s = mk_session(o, at, "CONVERTED", True, True, True)
        mk_pay(o, at, "CREATED", session=s)
        mk_pay(o, at + timedelta(minutes=40), "COMPLETED", session=s)
    # c) 3 historical recoveries: declined -> reminder -> completed
    for i, (d, issue) in enumerate([(21, "INSTRUMENT_DECLINED"), (12, "PAYER_ACTION_REQUIRED"), (5, "INSTRUMENT_DECLINED")]):
        at = ago(days=d, hours=3)
        o = mk_order(customers[[2, 5, 9][i]], products[[1, 3, 0][i]], at)
        s = mk_session(o, at, "CONVERTED", True, True, True)
        failed = mk_pay(o, at, "DECLINED", issue=issue, session=s)
        failed.failure_category = "CARD_DECLINED" if issue == "INSTRUMENT_DECLINED" else "AUTHENTICATION_REQUIRED"
        case = RecoveryCase(merchant_id=mid, order_id=o.id, customer_id=o.customer_id, payment_id=failed.id, checkout_session_id=s.id,
                            case_type="FAILED_PAYMENT", status="RECOVERED", failure_category=failed.failure_category,
                            amount_minor=o.amount_minor, recovery_probability=0.62, data_source="DEMO_SIMULATED",
                            reminders_sent=1 + (i == 1), created_at=at, last_contacted_at=at + timedelta(hours=1))
        db.add(case)
        db.flush()
        atype = "REQUEST_CUSTOMER_ACTION" if issue == "PAYER_ACTION_REQUIRED" else "RECOMMEND_ALTERNATIVE_METHOD"
        for k in range(1 + (i == 1)):
            db.add(RecoveryAction(merchant_id=mid, case_id=case.id, action_type=atype, status="EXECUTED", requires_approval=True,
                                  idempotency_key=f"{atype}:{k + 1}", reason="Demo history", executed_at=at + timedelta(hours=1 + 24 * k),
                                  result={"delivery_status": "PREVIEW_ONLY", "simulated": True}))
            db.add(CommunicationLog(merchant_id=mid, case_id=case.id, customer_id=o.customer_id, tone="friendly",
                                    subject="Your order is almost complete!", body="[Demo history message]", delivery_status="PREVIEW_ONLY",
                                    provider="demo", generated_by="rules", created_at=at + timedelta(hours=1 + 24 * k)))
        rec_at = at + timedelta(hours=[6, 30, 4][i])
        win = mk_pay(o, rec_at, "COMPLETED", session=s)
        win.is_recovery_attempt, win.recovery_case_id = True, case.id
        o.status, o.paid_at = "PAID", rec_at
        case.recovered_payment_id, case.recovered_amount_minor, case.recovered_at, case.closed_at = win.id, win.amount_minor, rec_at, rec_at
    # d) 1 unrecovered historical case
    at = ago(days=16)
    o = mk_order(customers[11], products[4], at)
    s = mk_session(o, at, "EXPIRED", True, True)
    f = mk_pay(o, at, "DECLINED", issue="INSTRUMENT_DECLINED", session=s)
    f.failure_category = "CARD_DECLINED"
    case = RecoveryCase(merchant_id=mid, order_id=o.id, customer_id=o.customer_id, payment_id=f.id, case_type="FAILED_PAYMENT",
                        status="CLOSED_UNRECOVERED", failure_category="CARD_DECLINED", amount_minor=o.amount_minor, reminders_sent=3,
                        data_source="DEMO_SIMULATED", created_at=at, closed_at=at + timedelta(days=3))
    db.add(case)
    db.flush()
    db.add(RecoveryAction(merchant_id=mid, case_id=case.id, action_type="RECOMMEND_ALTERNATIVE_METHOD", status="EXECUTED", requires_approval=True,
                          idempotency_key="RECOMMEND_ALTERNATIVE_METHOD:1", reason="Demo history", executed_at=at + timedelta(hours=1),
                          result={"simulated": True}))

    # e) 10 open failed orders (11 failed attempts), incl. hero TXN-2048
    open_failed = []
    hero_at = ago(hours=2)
    o = mk_order(customers[0], products[0], hero_at)
    s = mk_session(o, hero_at, "EXPIRED", True, True)
    open_failed.append(mk_pay(o, hero_at, "DECLINED", issue="PAYER_ACTION_REQUIRED", ref="TXN-2048", session=s))
    for i, (st, issue) in enumerate(FAILED_ISSUES[:9]):
        at = ago(days=rng.choice([0, 0, 1, 1, 2, 3, 4]), hours=rng.randint(1, 20))
        p_ = products[6] if i == 0 else prod()
        o = mk_order(pick(), p_, at)
        s = mk_session(o, at, "EXPIRED", True, True)
        if i in (1, 2):
            mk_pay(o, at - timedelta(minutes=30), "CREATED", session=s)
        if i == 3:
            mk_pay(o, at - timedelta(minutes=20), "DECLINED", issue="INSTRUMENT_DECLINED", session=s)
        open_failed.append(mk_pay(o, at, st, issue=issue, session=s))
    # f) 8 pending
    for i, reason in enumerate(PENDING_REASONS):
        at = ago(days=rng.randint(0, 3), hours=rng.randint(1, 12)) if i < 5 else ago(days=rng.randint(2, 5))
        o = mk_order(pick(), prod(), at)
        s = mk_session(o, at, "EXPIRED", True, True)
        mk_pay(o, at, "PENDING", issue=reason, session=s)
    # g) 5 approved but not captured
    for _ in range(5):
        at = ago(days=rng.randint(0, 2), hours=rng.randint(2, 10))
        o = mk_order(pick(), prod(), at)
        s = mk_session(o, at, "EXPIRED", True, True)
        mk_pay(o, at, "APPROVED", session=s)
    # h) 10 abandoned checkouts (6 with a PayPal order created, 4 without)
    for i in range(10):
        at = ago(days=rng.randint(0, 6), hours=rng.randint(1, 20))
        o = mk_order(pick(), prod(), at)
        s = mk_session(o, at, "ABANDONED", created=i < 6)
        if i < 6:
            mk_pay(o, at, "CREATED", session=s)
    db.flush()

    # Let the agent analyse open problems exactly as it would for live data
    for p in open_failed:
        case = agent.ensure_case(db, payment=p)
        if case:
            agent.analyse_case(db, case, payment=p)
            agent.plan_actions(db, case)
    agent.tick(db, mid)
    for n in db.scalars(select(Notification).where(Notification.merchant_id == mid)):
        n.read = True  # seed noise starts read; new events arrive unread
    agent.audit(db, mid, "system", "demo.seeded", "Demo workspace seeded with labelled simulated data")
    agent.notify(db, mid, "info", "Welcome to the RecoverAI demo",
                 "All data in this workspace is simulated and labelled DEMO. Connect PayPal Sandbox in Integrations to run real sandbox checkouts.",
                 link="/integrations")


def reset_demo(db: Session, merchant: Merchant):
    wipe_merchant_data(db, merchant.id)
    seed_demo_data(db, merchant)
    db.commit()
