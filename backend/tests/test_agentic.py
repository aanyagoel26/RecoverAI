"""Failure Lab (real sandbox negative testing), recovery rails, PayPal Invoicing rail, Trust Firewall traces."""
import json

from sqlalchemy import select

from app.models import AgentRun, Payment, PayPalInvoice, RecoveryCase
from app.services.paypal_client import PayPalClient, PayPalError

from .conftest import WEBHOOK_HEADERS


def test_lab_real_decline_routes_to_pay_later(client, demo, fake_paypal, db):
    r = client.post("/api/lab/run", json={"scenario": "card_declined", "customer_email": "buyer@personal.example.com"}, headers=demo).json()
    assert r["data_source"] == "PAYPAL_SANDBOX" and r["paypal"]["issue"] == "INSTRUMENT_DECLINED" and r["paypal"]["debug_id"]
    assert fake_paypal.mocks == ["INSTRUMENT_DECLINED"]  # negative-testing header really sent to PayPal
    p = db.get(Payment, r["payment_id"])
    assert p.status == "DECLINED" and p.status_verified_by == "PAYPAL_API" and p.raw_response["negative_testing"]["returned"] == "INSTRUMENT_DECLINED"
    case = client.get(f"/api/recovery/cases/{r['case_id']}", headers=demo).json()
    rec = case["analysis"]["recommendation"]
    assert rec["rail"]["key"] == "PAY_LATER" and rec["action"] == "RECOMMEND_ALTERNATIVE_METHOD"
    run = case["runs"][0]
    assert [s["key"] for s in run["steps"]] == ["detect", "retrieve", "analyse", "score", "decide", "guardrails", "act", "monitor", "analytics"]
    assert run["outcome"] == "AWAITING_APPROVAL" and "PayPal API" in run["trigger"]
    assert {g["key"] for g in run["guardrails"]} >= {"no_money_movement", "not_paid", "no_pending", "consent", "limit", "approval"}
    appr = client.post(f"/api/recovery/{r['case_id']}/approve", json={}, headers=demo).json()
    assert appr["action"]["status"] == "EXECUTED" and "rail=paylater" in appr["action"]["result"]["recovery_link"]


def test_high_value_uses_paypal_invoice_and_webhook_recovers(client, demo, fake_paypal, db):
    r = client.post("/api/lab/run", json={"scenario": "high_value"}, headers=demo).json()
    case = client.get(f"/api/recovery/cases/{r['case_id']}", headers=demo).json()
    assert case["analysis"]["recommendation"]["action"] == "SEND_PAYPAL_INVOICE"
    appr = client.post(f"/api/recovery/{r['case_id']}/approve", json={}, headers=demo).json()
    assert appr["action"]["status"] == "EXECUTED"
    inv = db.scalar(select(PayPalInvoice).where(PayPalInvoice.case_id == r["case_id"]))
    assert inv.status == "SENT" and inv.payer_view_url.startswith("https://www.sandbox.paypal.com/invoice/")
    assert appr["action"]["result"]["recovery_link"] == inv.payer_view_url
    before = client.get("/api/analytics/overview", headers=demo).json()["revenue_recovered_minor"]
    ev = {"id": "WH-INV-1", "event_type": "INVOICING.INVOICE.PAID",
          "resource": {"id": inv.paypal_invoice_id, "status": "PAID", "payments": {"transactions": [{"payment_id": "7XK12345AB"}]}}}
    assert client.post("/api/webhooks/paypal", content=json.dumps(ev), headers=WEBHOOK_HEADERS).json()["new_status"] == "COMPLETED"
    assert client.post("/api/webhooks/paypal", content=json.dumps(ev), headers=WEBHOOK_HEADERS).json()["status"] == "duplicate"
    c = db.get(RecoveryCase, r["case_id"])
    db.refresh(c)
    assert c.status == "RECOVERED" and c.recovered_amount_minor == 150000
    after = client.get("/api/analytics/overview", headers=demo).json()
    assert after["revenue_recovered_minor"] == before + 150000 and after["recovered_by_source"].get("PAYPAL_SANDBOX") == 1


def test_invoice_sync_via_api(client, demo, fake_paypal, db):
    r = client.post("/api/lab/run", json={"scenario": "high_value"}, headers=demo).json()
    client.post(f"/api/recovery/{r['case_id']}/approve", json={}, headers=demo)
    inv = db.scalar(select(PayPalInvoice).where(PayPalInvoice.case_id == r["case_id"]))
    assert client.post(f"/api/recovery/{r['case_id']}/invoice/sync", headers=demo).json()["invoice"]["status"] == "SENT"
    fake_paypal.invoices[inv.paypal_invoice_id]["status"] = "PAID"
    assert client.post(f"/api/recovery/{r['case_id']}/invoice/sync", headers=demo).json()["case_status"] == "RECOVERED"


def test_lab_without_paypal_is_labelled_demo(client, demo, db):
    r = client.post("/api/lab/run", json={"scenario": "payer_action"}, headers=demo).json()
    assert r["data_source"] == "DEMO_SIMULATED" and r["paypal"]["simulated"] is True
    assert db.get(Payment, r["payment_id"]).status_verified_by == "DEMO_SIMULATOR"


def test_lab_abandoned_creates_real_order_and_venmo_rail(client, demo, fake_paypal):
    r = client.post("/api/lab/run", json={"scenario": "abandoned"}, headers=demo).json()
    assert r["paypal"]["paypal_order_id"].startswith("5O")
    case = client.get(f"/api/recovery/cases/{r['case_id']}", headers=demo).json()
    assert case["case"]["case_type"] == "ABANDONED_CHECKOUT" and case["analysis"]["recommendation"]["rail"]["key"] == "VENMO"


def test_negative_testing_refused_against_live():
    c = PayPalClient("id", "secret", "https://api-m.paypal.com")
    try:
        c._mock("INSTRUMENT_DECLINED")
        raise AssertionError("should refuse")
    except PayPalError as e:
        assert e.name == "NEGATIVE_TESTING_DISABLED"


def test_trust_firewall_blocks_and_traces(client, demo, fake_paypal, db):
    r = client.post("/api/lab/run", json={"scenario": "card_declined"}, headers=demo).json()
    c = db.get(RecoveryCase, r["case_id"])
    c.customer.recovery_contact_consent = False
    db.commit()
    a = client.post(f"/api/recovery/{r['case_id']}/approve", json={}, headers=demo).json()["action"]
    assert a["status"] == "BLOCKED"
    run = db.scalars(select(AgentRun).where(AgentRun.case_id == r["case_id"]).order_by(AgentRun.created_at.desc())).first()
    assert run.outcome == "BLOCKED" and any(g["key"] == "consent" and g["status"] == "fail" for g in run.guardrails)


def test_runs_endpoint(client, demo):
    items = client.get("/api/agent/runs", headers=demo).json()["items"]
    assert items and all(len(i["steps"]) >= 4 for i in items)
