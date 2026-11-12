"""Vault, provider-independent AI, safe retry/reconciliation, auth extras, automation policy, intelligence, SMS signatures."""
import json

import httpx
from sqlalchemy import select

from app.models import AIUsage, IntegrationCredential, MerchantSettings, Payment, RecoveryAction, RecoveryCase
from app.services import llm, mailer, providers
from app.services.communication import twilio_signature_valid

from .conftest import WEBHOOK_HEADERS


def _save_paypal(client, h, webhook_id="WH-DASH-1"):
    r = client.put("/api/integrations/paypal_sandbox", json={"values": {"client_id": "AXdashboardclientid0001", "client_secret": "dash-secret-9876",
                                                                       "webhook_id": webhook_id}}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- vault
def test_vault_masks_and_encrypts(client, demo, db):
    out = _save_paypal(client, demo)
    assert out["masked"]["client_secret"].endswith("9876") and "dash-secret" not in json.dumps(out)
    listing = client.get("/api/integrations", headers=demo).text
    assert "dash-secret-9876" not in listing and "••••" in listing
    row = db.scalar(select(IntegrationCredential).where(IntegrationCredential.provider == "paypal_sandbox"))
    assert "dash-secret" not in row.encrypted_payload  # encrypted at rest
    # blank secret on update keeps the old one
    client.put("/api/integrations/paypal_sandbox", json={"values": {"client_id": "AXnewclientid000000002", "client_secret": ""}}, headers=demo)
    m = row.merchant_id
    assert providers.load(db, m, "paypal_sandbox")["client_secret"] == "dash-secret-9876"


def test_dashboard_credentials_drive_real_paypal_and_webhooks(client, demo, fake_paypal, db):
    from app.config import get_settings
    s = get_settings()
    s.paypal_client_id = s.paypal_client_secret = s.paypal_webhook_id = ""  # only the dashboard vault is configured
    _save_paypal(client, demo)
    assert client.post("/api/integrations/paypal_sandbox/test", json={}, headers=demo).json()["ok"] is True
    r = client.post("/api/lab/run", json={"scenario": "card_declined"}, headers=demo).json()
    assert r["data_source"] == "PAYPAL_SANDBOX" and fake_paypal.mocks == ["INSTRUMENT_DECLINED"]
    mid = db.get(Payment, r["payment_id"]).merchant_id
    token = client.post(f"/api/recovery/{r['case_id']}/approve", json={}, headers=demo).json()["action"]["result"]["recovery_link"].split("/pay/")[1].split("?")[0]
    oid = client.post(f"/api/public/checkout/{token}/create-order").json()["id"]
    client.post(f"/api/public/checkout/{token}/capture", json={"paypal_order_id": oid})
    p = db.scalar(select(Payment).where(Payment.paypal_order_id == oid))
    ev = {"id": "WH-M-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": p.paypal_capture_id}}
    res = client.post(f"/api/webhooks/paypal/m/{mid}", content=json.dumps(ev), headers=WEBHOOK_HEADERS)
    assert res.status_code == 200 and res.json()["status"] == "processed"
    verify = [c for c in fake_paypal.calls if c[1] == "/v1/notifications/verify-webhook-signature"]
    assert verify  # verified with the merchant's own credentials


def test_live_switch_requires_confirmation(client, demo):
    assert client.post("/api/paypal/environment", json={"environment": "live"}, headers=demo).status_code == 400
    assert client.post("/api/paypal/environment", json={"environment": "live", "confirm_live": True}, headers=demo).json()["environment"] == "live"
    assert client.get("/api/paypal/status", headers=demo).json()["credentials_configured"] is False  # sandbox creds never used for live


# ---------------------------------------------------------------- AI provider layer
def test_openai_provider_tool_loop_and_usage(monkeypatch, client, demo, db):
    client.put("/api/integrations/openai", json={"values": {"api_key": "sk-test-openai-123456", "model": "gpt-test"}}, headers=demo)
    client.put("/api/integrations-ai-provider", json={"ai_provider": "openai"}, headers=demo)
    calls = []

    def fake_post(url, headers, body):
        calls.append(body)
        assert url.endswith("/chat/completions") and headers["Authorization"] == "Bearer sk-test-openai-123456"
        if len(calls) == 1:
            assert body["tools"][0]["type"] == "function"
            msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
                                                                          "function": {"name": "get_recovery_cases", "arguments": "{\"limit\": 2}"}}]}
        else:
            assert body["messages"][-1]["role"] == "tool" and body["messages"][-1]["tool_call_id"] == "call_1"
            msg = {"role": "assistant", "content": "Two cases need attention."}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    monkeypatch.setattr(llm, "_post", fake_post)
    r = client.post("/api/ai/chat", json={"message": "What needs attention?"}, headers=demo).json()
    assert r["generated_by"] == "llm" and r["answer"] == "Two cases need attention."
    rows = db.scalars(select(AIUsage).where(AIUsage.purpose == "copilot")).all()
    assert len(rows) == 2 and all(x.success and x.provider == "openai" for x in rows)


def test_ai_unavailable_is_transparent(client, demo):
    st = client.get("/api/integrations", headers=demo).json()
    assert st["active_ai"] is None
    out = client.post("/api/intelligence/ai-summary", headers=demo).json()
    assert out["available"] is False and out["generated_by"] is None


# ---------------------------------------------------------------- safe retry
def test_timeout_is_not_failure_and_unsafe_retry_blocked(client, demo, fake_paypal, db):
    r = client.post("/api/lab/run", json={"scenario": "timeout"}, headers=demo).json()
    p = db.get(Payment, r["payment_id"])
    assert p.status == "CREATED" and p.reconcile_required is True  # never marked failed
    case = client.get(f"/api/recovery/cases/{r['case_id']}", headers=demo).json()
    assert case["analysis"]["recommendation"]["action"] == "RETRY_CAPTURE"
    # Approval mode (demo default): the internal retry is only proposed, never auto-executed
    assert [x["status"] for x in case["case"]["actions"] if x["action_type"] == "RETRY_CAPTURE"] == ["PROPOSED"]
    a = client.post(f"/api/recovery/{r['case_id']}/approve", json={}, headers=demo).json()["action"]
    assert a["status"] == "BLOCKED" and "only the buyer can approve" in a["blocked_reason"]
    assert not [c for c in fake_paypal.calls if c[1].endswith("/capture") and "paypal-mock-response" not in c[2]]  # no real capture retried
    follow = db.scalars(select(RecoveryAction).where(RecoveryAction.case_id == r["case_id"], RecoveryAction.status == "PROPOSED")).all()
    assert any(f.action_type == "REQUEST_CUSTOMER_ACTION" for f in follow)
    blocked = client.get("/api/firewall/blocked", headers=demo).json()["items"]
    assert blocked and any(g["key"] == "buyer_approved" and g["status"] == "fail" for g in blocked[0]["guardrails"])


def test_safe_retry_on_approved_order_uses_same_idempotency_key(client, demo, fake_paypal, db):
    _, token = None, None
    r = client.post("/api/lab/run", json={"scenario": "timeout"}, headers=demo).json()
    p = db.get(Payment, r["payment_id"])
    fake_paypal.orders[p.paypal_order_id]["status"] = "APPROVED"  # buyer had approved; the first capture's response was lost
    p.status = "APPROVED"
    db.commit()
    a = client.post(f"/api/recovery/{r['case_id']}/approve", json={}, headers=demo).json()["action"]
    assert a["status"] == "EXECUTED", a
    db.refresh(p)
    assert p.status == "COMPLETED" and p.reconcile_required is False
    caps = [c for c in fake_paypal.calls if c[1].endswith("/capture") and "paypal-mock-response" not in c[2]]
    assert caps and caps[0][2]["paypal-request-id"] == f"capture-{p.id}"
    c = db.get(RecoveryCase, r["case_id"])
    db.refresh(c)
    assert c.status == "CLOSED_ALREADY_PAID"  # its own payment completed: not counted as a 'recovery'


def test_concurrent_retry_is_locked(client, demo, fake_paypal, db):
    from app.services import retry
    r = client.post("/api/lab/run", json={"scenario": "timeout"}, headers=demo).json()
    p = db.get(Payment, r["payment_id"])
    lock = retry._lock_for(p.id)
    lock.acquire()
    try:
        a = client.post(f"/api/recovery/{r['case_id']}/approve", json={}, headers=demo).json()["action"]
        assert a["status"] == "BLOCKED" and "already running" in a["blocked_reason"]
    finally:
        lock.release()


def test_capture_5xx_flags_reconcile_not_failure(client, demo, fake_paypal, db):
    from tests.test_paypal import _hero_link
    _, token = _hero_link(client, demo)
    oid = client.post(f"/api/public/checkout/{token}/create-order").json()["id"]
    fake_paypal.capture_error = {"name": "INTERNAL_SERVER_ERROR", "message": "boom"}
    orig = fake_paypal.handler

    def h(req):
        if req.url.path.endswith("/capture"):
            return httpx.Response(503, json={"name": "SERVICE_UNAVAILABLE", "message": "down"})
        return orig(req)
    from app.services import paypal_client
    paypal_client.set_transport_for_tests(httpx.MockTransport(h))
    res = client.post(f"/api/public/checkout/{token}/capture", json={"paypal_order_id": oid})
    assert res.status_code == 502 and res.json()["detail"]["uncertain"] is True
    p = db.scalar(select(Payment).where(Payment.paypal_order_id == oid))
    assert p.status == "CREATED" and p.reconcile_required


def test_partial_refund_state(client, demo, fake_paypal, db):
    from tests.test_paypal import _hero_link
    _, token = _hero_link(client, demo)
    oid = client.post(f"/api/public/checkout/{token}/create-order").json()["id"]
    client.post(f"/api/public/checkout/{token}/capture", json={"paypal_order_id": oid})
    p = db.scalar(select(Payment).where(Payment.paypal_order_id == oid))
    ev = {"id": "WH-RF-1", "event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": {"id": "RF1", "amount": {"value": "50.00", "currency_code": "USD"},
          "links": [{"rel": "up", "href": f"https://api.sandbox.paypal.com/v2/payments/captures/{p.paypal_capture_id}"}]}}
    assert client.post("/api/webhooks/paypal", content=json.dumps(ev), headers=WEBHOOK_HEADERS).json()["new_status"] == "PARTIALLY_REFUNDED"
    db.refresh(p)
    assert p.refunded_minor == 5000


# ---------------------------------------------------------------- automation policy
def test_automatic_mode_holds_large_amounts_for_approval(client, demo, db):
    ms = db.scalar(select(MerchantSettings))
    ms.recovery_mode, ms.max_auto_amount_minor, ms.quiet_hours_start, ms.quiet_hours_end = "AUTOMATIC", 1000, None, None
    db.commit()
    r = client.post("/api/lab/run", json={"scenario": "card_declined"}, headers=demo).json()  # $249 > $10 limit
    from datetime import datetime, timedelta, timezone
    for a in db.scalars(select(RecoveryAction).where(RecoveryAction.case_id == r["case_id"], RecoveryAction.status == "SCHEDULED")):
        a.scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    client.post("/api/agent/run", headers=demo)
    acts = db.scalars(select(RecoveryAction).where(RecoveryAction.case_id == r["case_id"])).all()
    assert any(a.status == "PROPOSED" and a.requires_approval for a in acts)
    assert not any(a.status == "EXECUTED" and a.action_type == "RECOMMEND_ALTERNATIVE_METHOD" for a in acts)


def test_settings_validation(client, demo):
    assert client.put("/api/merchant/settings", json={"permitted_auto_actions": ["CAPTURE_MONEY"]}, headers=demo).status_code == 422
    assert client.put("/api/merchant/settings", json={"channels": ["fax"]}, headers=demo).status_code == 422
    ok = client.put("/api/merchant/settings", json={"max_retry_attempts": 3, "quiet_hours_start": None}, headers=demo).json()
    assert ok["max_retry_attempts"] == 3 and ok["quiet_hours_start"] is None
    assert client.put("/api/merchant/profile", json={"timezone": "Mars/Base"}, headers=demo).status_code == 422
    assert client.put("/api/merchant/profile", json={"timezone": "Asia/Kolkata"}, headers=demo).status_code == 200


def test_cancel_order_blocks_further_recovery(client, demo, db):
    case = db.scalar(select(RecoveryCase).where(RecoveryCase.status == "AWAITING_APPROVAL").limit(1))
    assert client.post(f"/api/orders/{case.order_id}/cancel", json={"reason": "customer asked"}, headers=demo).json()["order_status"] == "CANCELLED"
    db.refresh(case)
    assert case.status == "CLOSED_UNRECOVERED"
    assert not db.scalars(select(RecoveryAction).where(RecoveryAction.case_id == case.id, RecoveryAction.status.in_(["PROPOSED", "SCHEDULED"]))).all()


# ---------------------------------------------------------------- auth extras
def test_password_reset_flow_and_no_enumeration(monkeypatch, client):
    links = []
    monkeypatch.setattr(mailer, "send", lambda db, mid, to, subject, text, dev_link=None: links.append(dev_link) or "NOT_CONFIGURED")
    client.post("/api/auth/signup", json={"email": "reset@shopdemo.io", "password": "original-pass-1", "full_name": "R", "business_name": "B"})
    a = client.post("/api/auth/forgot-password", json={"email": "reset@shopdemo.io"}).json()
    b = client.post("/api/auth/forgot-password", json={"email": "nobody@shopdemo.io"}).json()
    assert a == b  # identical response either way
    token = [l for l in links if "reset-password" in l][-1].split("token=")[1]
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "brand-new-pass-2"}).json()["ok"]
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "another-pass-3"}).status_code == 400  # single use
    assert client.post("/api/auth/login", json={"email": "reset@shopdemo.io", "password": "brand-new-pass-2"}).status_code == 200


def test_email_verification_and_logout_all(monkeypatch, client):
    links = []
    monkeypatch.setattr(mailer, "send", lambda db, mid, to, subject, text, dev_link=None: links.append(dev_link) or "NOT_CONFIGURED")
    r = client.post("/api/auth/signup", json={"email": "verify@shopdemo.io", "password": "original-pass-1", "full_name": "V", "business_name": "B"}).json()
    h = {"Authorization": "Bearer " + r["token"]}
    assert r["user"]["email_verified"] is False and r["verification_email"] == "NOT_CONFIGURED"
    token = [l for l in links if "verify-email" in l][-1].split("token=")[1]
    assert client.post("/api/auth/verify-email", json={"token": token}).json()["ok"]
    assert client.get("/api/auth/me", headers=h).json()["user"]["email_verified"] is True
    assert client.post("/api/auth/logout-all", headers=h).json()["ok"]
    assert client.get("/api/auth/me", headers=h).status_code == 401  # old session revoked


# ---------------------------------------------------------------- intelligence & SMS
def test_failure_intelligence_is_evidence_based(client, demo):
    r = client.get("/api/intelligence?days=30", headers=demo).json()
    assert r["totals"]["failed"] == sum(c["count"] for c in r["categories"])
    assert r["patterns"] and r["insights"] and all(i["generated_by"] == "computed" and i["evidence"] for i in r["insights"])
    only = client.get("/api/intelligence?days=30&category=CARD_DECLINED", headers=demo).json()
    assert all(c["category"] == "CARD_DECLINED" for c in only["categories"])


def test_twilio_signature():
    import base64
    import hashlib
    import hmac
    url, params = "https://api.example.com/api/webhooks/twilio/m1", {"MessageSid": "SM1", "MessageStatus": "delivered"}
    sig = base64.b64encode(hmac.new(b"tok", (url + "MessageSidSM1MessageStatusdelivered").encode(), hashlib.sha1).digest()).decode()
    assert twilio_signature_valid("tok", url, params, sig) and not twilio_signature_valid("tok", url, params, "bad")


def test_click_tracking(client, demo, db):
    from app.models import CommunicationLog
    case = db.scalar(select(RecoveryCase).where(RecoveryCase.status == "AWAITING_APPROVAL").limit(1))
    link = client.post(f"/api/recovery/{case.id}/approve", json={}, headers=demo).json()["action"]["result"]["recovery_link"]
    token, c = link.split("/pay/")[1].split("?")[0], link.split("c=")[1]
    client.post(f"/api/public/checkout/{token}/activity", json={"c": c})
    assert db.get(CommunicationLog, c).clicked_at is not None


def test_shared_demo_integrations_are_locked(client, demo, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "demo_integrations_locked", True)
    r = client.put("/api/integrations/anthropic", json={"values": {"api_key": "sk-ant-planted-123456"}}, headers=demo)
    assert r.status_code == 403 and "shared" in r.json()["detail"]
    assert client.post("/api/paypal/environment", json={"environment": "live", "confirm_live": True}, headers=demo).status_code == 403
    assert client.post("/api/integrations/resend/test", json={"to": "someone@example.com"}, headers=demo).status_code == 403
    assert client.get("/api/integrations", headers=demo).status_code == 200  # still viewable


def test_passive_actions_show_pending_as_information_not_failure(client, demo, db):
    from app.services import policy
    case = db.scalars(select(RecoveryCase)).first()
    p = db.scalars(select(Payment).where(Payment.order_id == case.order_id)).first()
    p.status = "PENDING"
    db.flush()
    s = db.scalars(select(MerchantSettings)).first()
    wait = {c["key"]: c["status"] for c in policy.guardrails(db, case, s, "WAIT_FOR_PENDING")}
    remind = {c["key"]: c["status"] for c in policy.guardrails(db, case, s, "SEND_REMINDER")}
    assert wait["no_pending"] == "info"          # the pending capture is why the agent waits
    assert remind["no_pending"] == "fail"        # but it still blocks contacting the customer


def test_secrets_key_accepts_any_long_string(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "secrets_key", "not-a-fernet-key but long and random 123")
    f = providers._fernet()
    assert f.decrypt(f.encrypt(b"ok")) == b"ok"


# ---------------------------------------------------------------- Gemini (free tier) via OpenAI-compatible endpoint
def test_gemini_provider_tool_loop_echoes_thought_signatures(monkeypatch, client, demo, db):
    r = client.put("/api/integrations/gemini", json={"values": {"api_key": "AIzaSyTEST-gemini-123456"}}, headers=demo)
    assert r.status_code == 200 and "AIzaSyTEST-gemini-123456" not in r.text
    client.put("/api/integrations-ai-provider", json={"ai_provider": "gemini"}, headers=demo)
    calls = []
    sig = {"google": {"thought_signature": "c2lnbmF0dXJlLWJ5dGVz"}}

    def fake_post(url, headers, body):
        calls.append(body)
        assert url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        assert headers["Authorization"] == "Bearer AIzaSyTEST-gemini-123456"
        assert body["model"] == "gemini-3.8-flash" and "max_completion_tokens" not in body
        assert body["max_tokens"] > 2048 and body["reasoning_effort"] == "low"
        if len(calls) == 1:
            msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "fn_1", "type": "function", "extra_content": sig,
                                                                          "function": {"name": "get_recovery_cases", "arguments": "{\"limit\": 2}"}}]}
        else:
            assistant = [m for m in body["messages"] if m["role"] == "assistant" and m.get("tool_calls")][-1]
            assert assistant["tool_calls"][0]["extra_content"] == sig      # signature echoed back unchanged
            msg = {"role": "assistant", "content": "Two cases need attention."}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"prompt_tokens": 12, "completion_tokens": 6}})
    monkeypatch.setattr(llm, "_post", fake_post)
    out = client.post("/api/ai/chat", json={"message": "What needs attention?"}, headers=demo).json()
    assert out["generated_by"] == "llm" and out["answer"] == "Two cases need attention."
    rows = db.scalars(select(AIUsage).where(AIUsage.purpose == "copilot")).all()
    assert rows and all(x.provider == "gemini" and x.model == "gemini-3.8-flash" for x in rows)
    st = client.get("/api/integrations", headers=demo).json()
    assert st["active_ai"]["provider"] == "gemini"


def test_gemini_free_tier_rate_limit_falls_back_transparently(monkeypatch, client, demo):
    client.put("/api/integrations/gemini", json={"values": {"api_key": "AIzaSyTEST-gemini-123456"}}, headers=demo)
    client.put("/api/integrations-ai-provider", json={"ai_provider": "gemini"}, headers=demo)
    monkeypatch.setattr(llm, "_post", lambda url, headers, body: httpx.Response(
        429, json={"error": {"code": 429, "message": "Resource has been exhausted (e.g. check quota)."}}))
    out = client.post("/api/intelligence/ai-summary", headers=demo).json()
    assert out["available"] is False and "free-tier limit" in out["reason"]
    t = client.post("/api/integrations/gemini/test", json={}, headers=demo).json()
    assert t["ok"] is False and "rate limit" in t["message"]


def test_auto_mode_uses_gemini_when_it_is_the_only_key(client, demo):
    client.put("/api/integrations/gemini", json={"values": {"api_key": "AIzaSyTEST-gemini-123456", "model": "gemini-3.6-flash"}}, headers=demo)
    client.put("/api/integrations-ai-provider", json={"ai_provider": "auto"}, headers=demo)
    a = client.get("/api/integrations", headers=demo).json()["active_ai"]
    assert a["provider"] == "gemini" and a["model"] == "gemini-3.6-flash"
