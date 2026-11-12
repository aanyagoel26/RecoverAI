"""Auth, isolation, policy engine, classification, scoring, revenue maths, AI recommendation validation."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Customer, Merchant, Order, Payment, RecoveryAction, RecoveryCase
from app.services import agent, analytics, classifier, policy


def signup(client, email="owner@shopdemo.io", business="Shop Two"):
    r = client.post("/api/auth/signup", json={"email": email, "password": "correct-horse-battery", "full_name": "Sam Owner", "business_name": business})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


# ---------------------------------------------------------------- authentication
def test_signup_login_me(client):
    signup(client)
    r = client.post("/api/auth/login", json={"email": "owner@shopdemo.io", "password": "correct-horse-battery"})
    assert r.status_code == 200
    me = client.get("/api/auth/me", headers={"Authorization": "Bearer " + r.json()["token"]}).json()
    assert me["merchant"]["name"] == "Shop Two" and me["user"]["role"] == "owner"


def test_bad_login_and_missing_token(client):
    signup(client)
    assert client.post("/api/auth/login", json={"email": "owner@shopdemo.io", "password": "wrong-password!"}).status_code == 401
    assert client.get("/api/payments").status_code == 401
    assert client.get("/api/payments", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_weak_password_rejected(client):
    r = client.post("/api/auth/signup", json={"email": "a@b.co", "password": "short", "full_name": "x", "business_name": "y"})
    assert r.status_code == 422


# ---------------------------------------------------------------- merchant isolation
def test_merchant_cannot_see_other_merchants_data(client, demo):
    other = signup(client)
    demo_payment = client.get("/api/payments?limit=1", headers=demo).json()["items"][0]
    assert client.get("/api/payments", headers=other).json()["total"] == 0
    assert client.get(f"/api/payments/{demo_payment['id']}", headers=other).status_code == 404
    case = client.get("/api/recovery/cases", headers=demo).json()["items"][0]
    assert client.get(f"/api/recovery/cases/{case['id']}", headers=other).status_code == 404
    assert client.post(f"/api/recovery/{case['id']}/approve", json={}, headers=other).status_code == 404
    assert client.get("/api/analytics/overview", headers=other).json()["total_attempts"] == 0
    assert client.post("/api/demo/reset", headers=other).status_code == 403


def test_analyst_role_cannot_approve(client, demo, db):
    from app.models import User
    from app.security import create_token, hash_password
    m = db.scalar(select(Merchant).where(Merchant.is_demo.is_(True)))
    u = User(merchant_id=m.id, email="analyst@shopdemo.io", full_name="Ana", password_hash=hash_password("x" * 12), role="analyst")
    db.add(u)
    db.commit()
    h = {"Authorization": "Bearer " + create_token(u)}
    case = client.get("/api/recovery/cases?view=approval", headers=h).json()["items"][0]
    assert client.post(f"/api/recovery/{case['id']}/approve", json={}, headers=h).status_code == 403


# ---------------------------------------------------------------- payment state machine
def test_state_transitions():
    assert policy.can_transition("CREATED", "APPROVED")
    assert policy.can_transition("PENDING", "COMPLETED")
    assert not policy.can_transition("COMPLETED", "PENDING")  # late PENDING webhook can't regress
    assert not policy.can_transition("DECLINED", "COMPLETED")
    assert not policy.can_transition("REFUNDED", "COMPLETED")
    assert policy.can_transition("COMPLETED", "COMPLETED")  # idempotent


def test_out_of_order_event_rejected(client, demo, db):
    p = db.scalar(select(Payment).where(Payment.status == "COMPLETED").limit(1))
    changed = agent.apply_status(db, p, "PENDING", source="PAYPAL_WEBHOOK")
    db.commit()
    assert changed is False and p.status == "COMPLETED"


# ---------------------------------------------------------------- classification
def test_classification_uses_only_documented_signals():
    assert classifier.classify("DECLINED", "INSTRUMENT_DECLINED", {}).category == "CARD_DECLINED"
    assert classifier.classify("DECLINED", "PAYER_ACTION_REQUIRED", {}).category == "AUTHENTICATION_REQUIRED"
    assert classifier.classify("FAILED", "DUPLICATE_INVOICE_ID", {}).category == "DUPLICATE_ATTEMPT"
    assert classifier.classify("FAILED", "CURRENCY_NOT_SUPPORTED_FOR_RECEIVER", {}).category == "CONFIGURATION_ISSUE"
    assert classifier.classify("FAILED", "NETWORK_ERROR", {}).category == "TECHNICAL_ERROR"
    assert classifier.classify("APPROVED", None, {}).category == "CAPTURE_FAILED"


def test_unknown_when_evidence_missing_never_invented():
    c = classifier.classify("FAILED", None, {})
    assert c.category == "UNKNOWN"
    # processor codes are shown raw, never interpreted into a reason
    c2 = classifier.classify("FAILED", None, {"capture": {"status": "FAILED", "processor_response": {"response_code": "5120"}}})
    assert c2.category == "UNKNOWN" and any("not interpreted" in e for e in c2.evidence)


def test_hero_analysis(client, demo):
    p = client.get("/api/payments?q=TXN-2048", headers=demo).json()["items"][0]
    r = client.post(f"/api/payments/{p['id']}/analyse", headers=demo)
    a = r.json()["analysis"]
    assert a["failure_category"] == "AUTHENTICATION_REQUIRED"
    assert 0.5 < a["recovery_probability"] < 0.95
    rec = a["recommendation"]
    for k in ("action", "reason", "confidence", "evidence", "expected_benefit", "risk", "requires_approval"):
        assert k in rec
    assert rec["action"] == "REQUEST_CUSTOMER_ACTION" and rec["requires_approval"] is True
    assert a["generated_by"] == "rules"  # no LLM key in tests — labelled honestly


def test_completed_payment_cannot_be_analysed_for_recovery(client, demo):
    p = client.get("/api/payments?filter=successful&limit=1", headers=demo).json()["items"][0]
    assert client.post(f"/api/payments/{p['id']}/analyse", headers=demo).status_code == 409


# ---------------------------------------------------------------- recovery eligibility
def test_no_reminder_when_order_already_paid(client, demo, db):
    case = db.scalar(select(RecoveryCase).where(RecoveryCase.case_type == "ABANDONED_CHECKOUT", RecoveryCase.status == "AWAITING_APPROVAL").limit(1))
    order = db.get(Order, case.order_id)
    order.status = "PAID"
    db.commit()
    r = client.post(f"/api/recovery/{case.id}/approve", json={}, headers=demo)
    assert r.status_code == 200
    assert r.json()["action"]["status"] == "CANCELLED"
    assert r.json()["case"]["status"] == "CLOSED_ALREADY_PAID"


def test_no_contact_without_consent(client, demo, db):
    case = db.scalar(select(RecoveryCase).where(RecoveryCase.status == "AWAITING_APPROVAL").limit(1))
    db.get(Customer, case.customer_id).recovery_contact_consent = False
    db.commit()
    r = client.post(f"/api/recovery/{case.id}/approve", json={}, headers=demo).json()
    assert r["action"]["status"] == "BLOCKED" and "consent" in r["action"]["blocked_reason"]


def test_message_limits_enforced(client, demo, db):
    case = db.scalar(select(RecoveryCase).where(RecoveryCase.status == "AWAITING_APPROVAL").limit(1))
    first = client.post(f"/api/recovery/{case.id}/approve", json={}, headers=demo).json()
    assert first["action"]["status"] == "EXECUTED"
    second = db.scalar(select(RecoveryAction).where(RecoveryAction.case_id == case.id, RecoveryAction.status == "SCHEDULED"))
    r = client.post(f"/api/recovery/{case.id}/approve", json={"action_id": second.id}, headers=demo).json()
    assert r["action"]["status"] == "BLOCKED" and "minutes" in r["action"]["blocked_reason"]


def test_preview_only_email_is_not_claimed_as_sent(client, demo, db):
    case = db.scalar(select(RecoveryCase).where(RecoveryCase.status == "AWAITING_APPROVAL").limit(1))
    client.post(f"/api/recovery/{case.id}/approve", json={"tone": "professional"}, headers=demo)
    comms = client.get(f"/api/recovery/cases/{case.id}", headers=demo).json()["communications"]
    assert comms[0]["delivery_status"] == "PREVIEW_ONLY" and comms[0]["sent_at"] is None
    assert "/pay/" in comms[0]["recovery_link"] and comms[0]["recovery_link"] in comms[0]["body"]


def test_ai_recommendation_validated_against_policy(db, client, demo):
    """Whatever the LLM writes, the action comes from the policy engine and is in the allowed set."""
    for a in db.scalars(select(agent.AIAnalysis)):
        assert a.recommendation["action"] in policy.ACTIONS
        if a.failure_category in ("UNKNOWN", "CAPTURE_FAILED", "CONFIGURATION_ISSUE", "DUPLICATE_ATTEMPT"):
            assert a.recommendation["action"] not in policy.CUSTOMER_FACING


def test_llm_message_rejected_if_it_drops_amount(monkeypatch, client, demo, db):
    from app.services import communication, llm
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {"subject": "Hi", "body": "Pay now please, 50% off!"})
    case = db.scalar(select(RecoveryCase).where(RecoveryCase.status == "AWAITING_APPROVAL").limit(1))
    msg = communication.generate(db, case, tone="friendly", link="https://x.test/pay/abc", brand="B", support_email=None)
    assert msg["generated_by"] == "rules" and "50% off" not in msg["body"]


# ---------------------------------------------------------------- revenue calculations
def test_revenue_numbers_come_from_rows(client, demo, db):
    m = db.scalar(select(Merchant).where(Merchant.is_demo.is_(True)))
    o = analytics.overview(db, m.id, days=90)
    recovered = sum(c.recovered_amount_minor for c in db.scalars(select(RecoveryCase).where(RecoveryCase.status == "RECOVERED")))
    at_risk = sum(c.amount_minor for c in db.scalars(select(RecoveryCase).where(RecoveryCase.status.in_(agent.ACTIVE_CASE))))
    assert o["revenue_recovered_minor"] == recovered == 32900 + 45900 + 24900  # the three seeded historical recoveries
    assert o["revenue_at_risk_minor"] == at_risk
    completed = db.scalars(select(Payment).where(Payment.status == "COMPLETED")).all()
    assert o["revenue_processed_minor"] == sum(p.amount_minor for p in completed)
    assert o["successful"] == 12 and o["failed"] == 15 and o["pending"] == 8 and o["total_attempts"] == 50


def test_simulator_never_reports_estimates_as_actual(client, demo):
    r = client.post("/api/simulator/run", json={}, headers=demo).json()
    for s in r["scenarios"]:
        assert s["low_minor"] <= s["expected_minor"] <= s["high_minor"] <= s["at_risk_minor"]
    assert "not recovered revenue" in r["disclaimer"]
    before = client.get("/api/analytics/overview", headers=demo).json()["revenue_recovered_minor"]
    client.post("/api/simulator/run", json={"at_risk_override_minor": 1_000_000}, headers=demo)
    assert client.get("/api/analytics/overview", headers=demo).json()["revenue_recovered_minor"] == before
    assert client.post("/api/simulator/run", json={"assumptions": {"failed_base_rate": 3}}, headers=demo).status_code == 400


def test_copilot_rules_mode_uses_database(client, demo):
    r = client.post("/api/ai/chat", json={"message": "How much revenue did I recover this week?"}, headers=demo).json()
    assert r["generated_by"] == "rules" and r["tool_calls"][0]["tool"] == "get_payment_analytics"
    r2 = client.post("/api/ai/chat", json={"message": "Which payments need immediate attention?"}, headers=demo).json()
    assert r2["tool_calls"][0]["tool"] == "get_recovery_cases" and "1." in r2["answer"]
    r3 = client.post("/api/ai/chat", json={"message": "explain TXN-2048"}, headers=demo).json()
    assert "Customer Action" in r3["answer"] or "Authentication" in r3["answer"]


def test_agent_tick_detects_abandonment(client, demo, db):
    from app.models import CheckoutSession
    m = db.scalar(select(Merchant).where(Merchant.is_demo.is_(True)))
    c = db.scalars(select(Customer).where(Customer.merchant_id == m.id)).first()
    o = Order(merchant_id=m.id, customer_id=c.id, order_number="NW-T1", description="Test", amount_minor=5000)
    db.add(o)
    db.flush()
    db.add(CheckoutSession(merchant_id=m.id, order_id=o.id, customer_id=c.id, status="ACTIVE",
                           last_activity_at=datetime.now(timezone.utc) - timedelta(hours=2)))
    db.commit()
    s = client.post("/api/agent/run", headers=demo).json()["summary"]
    assert s["abandoned_detected"] == 1 and s["cases_opened"] >= 1
    case = db.scalar(select(RecoveryCase).where(RecoveryCase.order_id == o.id))
    assert case.case_type == "ABANDONED_CHECKOUT" and case.failure_category == "CHECKOUT_ABANDONED"


def test_follow_ups_are_sequential(client, demo, db):
    """At most one customer-facing proposal per case; follow-ups wait until the previous message went out."""
    from collections import Counter
    props = Counter(a.case_id for a in db.scalars(select(RecoveryAction).where(RecoveryAction.status == "PROPOSED")))
    assert props and max(props.values()) == 1
    client.post("/api/agent/run", headers=demo)
    props = Counter(a.case_id for a in db.scalars(select(RecoveryAction).where(RecoveryAction.status == "PROPOSED")))
    assert max(props.values()) == 1
