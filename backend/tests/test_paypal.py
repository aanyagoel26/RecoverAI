"""PayPal integration: OAuth, orders, capture, webhooks (verification, dedupe), duplicate prevention, end-to-end recovery."""
import json

import pytest
from sqlalchemy import select

from app.models import Payment, RecoveryCase, WebhookEvent
from app.services.paypal_client import get_paypal

from .conftest import WEBHOOK_HEADERS


def test_token_is_cached_and_server_side(fake_paypal):
    pp = get_paypal()
    t1, t2 = pp.access_token(), pp.access_token()
    assert t1 == t2 and fake_paypal.token_count == 1
    assert pp.access_token(force=True) != t1 and fake_paypal.token_count == 2


def test_token_refresh_on_401(fake_paypal):
    pp = get_paypal()
    pp.access_token()
    orig = fake_paypal.handler
    state = {"n": 0}

    def flaky(req):
        if req.url.path == "/v2/checkout/orders/X1" and state["n"] == 0:
            state["n"] += 1
            import httpx
            return httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE"})
        if req.url.path == "/v2/checkout/orders/X1":
            import httpx
            return httpx.Response(200, json={"id": "X1", "status": "APPROVED"})
        return orig(req)
    import httpx
    from app.services import paypal_client
    paypal_client.set_transport_for_tests(httpx.MockTransport(flaky))
    assert get_paypal().get_order("X1")["status"] == "APPROVED"


def test_client_config_never_exposes_secret(client, fake_paypal):
    body = client.get("/api/paypal/client-config").text
    assert "sb-secret" not in body and "sb-client-id" in body


def test_connection_test(client, demo, fake_paypal):
    r = client.post("/api/paypal/connect", headers=demo).json()
    assert r["ok"] is True
    st = client.get("/api/paypal/status", headers=demo).json()
    assert st["connection_status"] == "CONNECTED" and st["credentials_configured"] and st["webhook_id_configured"]


def _hero_link(client, demo):
    p = client.get("/api/payments?q=TXN-2048", headers=demo).json()["items"][0]
    case_id = client.post(f"/api/payments/{p['id']}/analyse", headers=demo).json()["case_id"]
    r = client.post(f"/api/recovery/{case_id}/approve", json={}, headers=demo).json()
    assert r["action"]["status"] == "EXECUTED", r
    return case_id, r["action"]["result"]["recovery_link"].split("/pay/")[1].split("?")[0]


def test_end_to_end_recovery_with_paypal_sandbox(client, demo, fake_paypal, db):
    before = client.get("/api/analytics/overview", headers=demo).json()["revenue_recovered_minor"]
    case_id, token = _hero_link(client, demo)
    page = client.get(f"/api/public/checkout/{token}").json()
    assert page["order"]["amount_minor"] == 24900 and not page["paid"] and page["paypal"]["enabled"]

    created = client.post(f"/api/public/checkout/{token}/create-order").json()
    oid = created["id"]
    create_call = [c for c in fake_paypal.calls if c[1] == "/v2/checkout/orders"][0]
    assert create_call[2].get("paypal-request-id"), "create order must send PayPal-Request-Id"
    body = fake_paypal.orders[oid]["purchase_units"][0]
    assert body["amount"] == {"currency_code": "USD", "value": "249.00"}

    # second click on PayPal button reuses the same PayPal order (no duplicate order)
    assert client.post(f"/api/public/checkout/{token}/create-order").json()["id"] == oid

    cap = client.post(f"/api/public/checkout/{token}/capture", json={"paypal_order_id": oid}).json()
    assert cap["status"] == "COMPLETED" and cap["verified_by"] == "PAYPAL_API"
    case = client.get(f"/api/recovery/cases/{case_id}", headers=demo).json()["case"]
    assert case["status"] == "RECOVERED" and case["recovered_amount_minor"] == 24900
    after = client.get("/api/analytics/overview", headers=demo).json()["revenue_recovered_minor"]
    assert after == before + 24900
    # remaining reminders cancelled
    assert all(a["status"] in ("EXECUTED", "CANCELLED") for a in case["actions"])
    p = db.scalar(select(Payment).where(Payment.paypal_order_id == oid))
    assert p.data_source == "PAYPAL_SANDBOX" and p.is_recovery_attempt

    # webhook for the same capture arrives later: verified, idempotent, no double count
    event = {"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": p.paypal_capture_id, "status": "COMPLETED",
             "supplementary_data": {"related_ids": {"order_id": oid}}}}
    r = client.post("/api/webhooks/paypal", content=json.dumps(event), headers=WEBHOOK_HEADERS)
    assert r.status_code == 200 and r.json()["status"] == "processed"
    r2 = client.post("/api/webhooks/paypal", content=json.dumps(event), headers=WEBHOOK_HEADERS)
    assert r2.json()["status"] == "duplicate"
    assert client.get("/api/analytics/overview", headers=demo).json()["revenue_recovered_minor"] == after
    # page now shows paid and refuses a new order
    assert client.get(f"/api/public/checkout/{token}").json()["paid"] is True
    assert client.post(f"/api/public/checkout/{token}/create-order").status_code == 409


def test_capture_is_never_repeated(client, demo, fake_paypal):
    _, token = _hero_link(client, demo)
    oid = client.post(f"/api/public/checkout/{token}/create-order").json()["id"]
    client.post(f"/api/public/checkout/{token}/capture", json={"paypal_order_id": oid})
    n = len([c for c in fake_paypal.calls if c[1].endswith("/capture")])
    again = client.post(f"/api/public/checkout/{token}/capture", json={"paypal_order_id": oid}).json()
    assert again["status"] == "COMPLETED"
    assert len([c for c in fake_paypal.calls if c[1].endswith("/capture")]) == n  # no second PayPal capture call
    cap_call = [c for c in fake_paypal.calls if c[1].endswith("/capture")][0]
    assert cap_call[2]["paypal-request-id"].startswith("capture-")


def test_instrument_declined_on_capture(client, demo, fake_paypal, db):
    _, token = _hero_link(client, demo)
    oid = client.post(f"/api/public/checkout/{token}/create-order").json()["id"]
    fake_paypal.capture_error = {"name": "UNPROCESSABLE_ENTITY", "message": "declined", "debug_id": "dbg1", "details": [{"issue": "INSTRUMENT_DECLINED"}]}
    r = client.post(f"/api/public/checkout/{token}/capture", json={"paypal_order_id": oid})
    assert r.status_code == 422 and r.json()["detail"]["issue"] == "INSTRUMENT_DECLINED"
    p = db.scalar(select(Payment).where(Payment.paypal_order_id == oid))
    assert p.status != "COMPLETED" and p.paypal_issue == "INSTRUMENT_DECLINED"


def test_pending_capture_waits_and_never_recharges(client, demo, fake_paypal, db):
    case_id, token = _hero_link(client, demo)
    oid = client.post(f"/api/public/checkout/{token}/create-order").json()["id"]
    fake_paypal.capture_outcome = "PENDING"
    assert client.post(f"/api/public/checkout/{token}/capture", json={"paypal_order_id": oid}).json()["status"] == "PENDING"
    assert client.post(f"/api/public/checkout/{token}/create-order").status_code == 409  # no new charge while pending
    case = client.get(f"/api/recovery/cases/{case_id}", headers=demo).json()["case"]
    assert case["status"] != "RECOVERED"
    assert not any(a["status"] in ("PROPOSED", "SCHEDULED") and a["action_type"] in ("REQUEST_CUSTOMER_ACTION", "SEND_REMINDER") for a in case["actions"])
    p = db.scalar(select(Payment).where(Payment.paypal_order_id == oid))
    event = {"id": "WH-P2", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": p.paypal_capture_id, "status": "COMPLETED"}}
    assert client.post("/api/webhooks/paypal", content=json.dumps(event), headers=WEBHOOK_HEADERS).json()["new_status"] == "COMPLETED"
    assert client.get(f"/api/recovery/cases/{case_id}", headers=demo).json()["case"]["status"] == "RECOVERED"


def test_webhook_rejected_when_signature_invalid(client, demo, fake_paypal, db):
    _, token = _hero_link(client, demo)
    oid = client.post(f"/api/public/checkout/{token}/create-order").json()["id"]
    fake_paypal.verify_result = "FAILURE"
    event = {"id": "WH-EVIL", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "CAPX", "supplementary_data": {"related_ids": {"order_id": oid}}}}
    r = client.post("/api/webhooks/paypal", content=json.dumps(event), headers=WEBHOOK_HEADERS)
    assert r.status_code == 400
    assert db.scalar(select(Payment).where(Payment.paypal_order_id == oid)).status == "CREATED"
    assert db.scalar(select(WebhookEvent).where(WebhookEvent.paypal_event_id == "WH-EVIL")).verification_status == "FAILURE"


def test_webhook_missing_headers_rejected(client, fake_paypal):
    event = {"id": "WH-NOHDR", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "C1"}}
    assert client.post("/api/webhooks/paypal", content=json.dumps(event), headers={"Content-Type": "application/json"}).status_code == 400


def test_webhook_not_trusted_when_unconfigured(client, demo, db):
    event = {"id": "WH-U", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "C1"}}
    r = client.post("/api/webhooks/paypal", content=json.dumps(event), headers=WEBHOOK_HEADERS)
    assert r.json()["status"] == "stored_unverified"


def test_frontend_redirect_is_not_proof(client, demo, fake_paypal, db):
    """Only capture via API/webhook changes status: hitting the return URL / page does nothing."""
    _, token = _hero_link(client, demo)
    oid = client.post(f"/api/public/checkout/{token}/create-order").json()["id"]
    client.get(f"/api/public/checkout/{token}?paypal=return&token={oid}")
    assert db.scalar(select(Payment).where(Payment.paypal_order_id == oid)).status == "CREATED"


def test_cannot_capture_order_owned_by_other_checkout(client, demo, fake_paypal):
    _, token = _hero_link(client, demo)
    r = client.post(f"/api/public/checkout/{token}/capture", json={"paypal_order_id": "5O999127TN364715T"})
    assert r.status_code == 404


def test_simulator_refuses_real_sandbox_orders(client, demo, fake_paypal):
    r = client.post("/api/paypal/test-checkout", json={}, headers=demo).json()
    assert r["data_source"] == "PAYPAL_SANDBOX"
    token = r["checkout_url"].split("/pay/")[1].split("?")[0]
    assert client.post(f"/api/public/checkout/{token}/simulate", json={"outcome": "complete"}).status_code == 403


def test_demo_simulator_journey_labelled(client, demo, db):
    case_id, token = _hero_link(client, demo)
    page = client.get(f"/api/public/checkout/{token}").json()
    assert page["paypal"]["enabled"] is False and page["simulator_allowed"] is True
    r = client.post(f"/api/public/checkout/{token}/simulate", json={"outcome": "complete"}).json()
    assert r["simulated"] and r["verified_by"] == "DEMO_SIMULATOR"
    case = db.get(RecoveryCase, case_id)
    db.refresh(case)
    assert case.status == "RECOVERED"
    p = db.get(Payment, case.recovered_payment_id)
    assert p.data_source == "DEMO_SIMULATED"


def test_duplicate_completed_payment_flagged(client, demo, db):
    case_id, token = _hero_link(client, demo)
    client.post(f"/api/public/checkout/{token}/simulate", json={"outcome": "complete"})
    assert client.post(f"/api/public/checkout/{token}/simulate", json={"outcome": "complete"}).status_code == 409


@pytest.mark.parametrize("event_type,expected", [("PAYMENT.CAPTURE.DENIED", "DECLINED"), ("PAYMENT.CAPTURE.PENDING", "PENDING")])
def test_simulated_webhooks(client, demo, db, event_type, expected):
    p = db.scalar(select(Payment).where(Payment.status == "APPROVED").limit(1))
    r = client.post("/api/demo/simulate-webhook", json={"payment_id": p.id, "event_type": event_type}, headers=demo).json()
    assert r["new_status"] == expected
    w = db.scalar(select(WebhookEvent).where(WebhookEvent.paypal_event_id == r["event_id"]))
    assert w.is_simulated and w.verification_status == "DEMO"
    rep = client.post("/api/demo/replay-webhook", json={"event_id": r["event_id"]}, headers=demo).json()
    assert rep["status"] == "duplicate"
