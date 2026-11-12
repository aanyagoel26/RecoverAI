import json
import os

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SEED_DEMO_ON_START"] = "false"
os.environ["DEMO_INTEGRATIONS_LOCKED"] = "false"  # most tests drive integrations through the demo merchant
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["RESEND_API_KEY"] = ""
os.environ["PAYPAL_CLIENT_ID"] = ""
os.environ["PAYPAL_CLIENT_SECRET"] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db as dbmod  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.services import paypal_client  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    eng = dbmod.reset_engine("sqlite://")
    Base.metadata.create_all(bind=eng)
    s = get_settings()
    s.paypal_client_id = s.paypal_client_secret = s.paypal_webhook_id = ""
    paypal_client.set_transport_for_tests(None)
    yield
    Base.metadata.drop_all(bind=eng)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    with dbmod.SessionLocal() as s:
        yield s


@pytest.fixture
def demo(client):
    r = client.post("/api/auth/demo")
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


class FakePayPal:
    """In-memory PayPal sandbox double implementing the documented request/response shapes."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.orders: dict[str, dict] = {}
        self.capture_outcome = "COMPLETED"
        self.capture_error: dict | None = None
        self.verify_result = "SUCCESS"
        self.token_count = 0
        self.request_ids: dict[str, dict] = {}
        self.mocks: list[str] = []
        self.invoices: dict[str, dict] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else {}
        self.calls.append((method, path, dict(request.headers)))
        rid = request.headers.get("paypal-request-id")
        if path == "/v1/oauth2/token":
            assert request.headers["authorization"].startswith("Basic ")
            self.token_count += 1
            return httpx.Response(200, json={"access_token": f"A21-token-{self.token_count}", "token_type": "Bearer", "expires_in": 32400})
        assert request.headers.get("authorization", "").startswith("Bearer A21-token"), "missing bearer token"
        if rid and rid in self.request_ids:  # PayPal idempotency replay
            return httpx.Response(200, json=self.request_ids[rid])
        if path == "/v2/checkout/orders" and method == "POST":
            oid = f"5O{len(self.orders) + 1:03d}127TN364715T"
            o = {"id": oid, "status": "PAYER_ACTION_REQUIRED", "purchase_units": body["purchase_units"],
                 "links": [{"href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}", "rel": "payer-action", "method": "GET"}]}
            self.orders[oid] = o
            if rid:
                self.request_ids[rid] = o
            return httpx.Response(200, json=o)
        if path.startswith("/v2/checkout/orders/") and path.endswith("/capture"):
            oid = path.split("/")[4]
            mock = request.headers.get("paypal-mock-response")
            if mock:  # PayPal sandbox negative testing
                code = json.loads(mock)["mock_application_codes"]
                self.mocks.append(code)
                if code == "INTERNAL_SERVER_ERROR":
                    return httpx.Response(500, json={"name": "INTERNAL_SERVER_ERROR", "message": "mocked", "debug_id": "5e7e7e7e"})
                return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY", "message": "mocked", "debug_id": "f00dbabe1234",
                                                 "details": [{"issue": code, "description": "negative test"}]})
            if self.capture_error:
                return httpx.Response(422, json=self.capture_error)
            if self.orders[oid].get("status") == "COMPLETED":
                return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY", "message": "already", "details": [{"issue": "ORDER_ALREADY_CAPTURED"}]})
            cap = {"id": f"CAP{oid[-6:]}", "status": self.capture_outcome, "amount": self.orders[oid]["purchase_units"][0]["amount"],
                   "custom_id": self.orders[oid]["purchase_units"][0]["custom_id"]}
            if self.capture_outcome == "PENDING":
                cap["status_details"] = {"reason": "PENDING_REVIEW"}
            o = self.orders[oid] | {"status": "COMPLETED", "purchase_units": [self.orders[oid]["purchase_units"][0] | {"payments": {"captures": [cap]}}]}
            self.orders[oid] = o
            if rid:
                self.request_ids[rid] = o
            return httpx.Response(201, json=o)
        if path.startswith("/v2/checkout/orders/") and method == "GET":
            return httpx.Response(200, json=self.orders[path.split("/")[4]])
        if path == "/v2/invoicing/invoices" and method == "POST":
            iid = f"INV2-TEST-{len(self.invoices) + 1:04d}"
            self.invoices[iid] = {"id": iid, "status": "DRAFT", "detail": body["detail"], "items": body["items"],
                                  "primary_recipients": body["primary_recipients"]}
            return httpx.Response(201, json=self.invoices[iid])
        if path.startswith("/v2/invoicing/invoices/") and path.endswith("/send"):
            iid = path.split("/")[4]
            self.invoices[iid]["status"] = "SENT"
            return httpx.Response(200, json={"href": f"https://www.sandbox.paypal.com/invoice/p/#{iid}", "rel": "payer-view", "method": "GET"})
        if path.startswith("/v2/invoicing/invoices/") and method == "GET":
            return httpx.Response(200, json=self.invoices[path.split("/")[4]])
        if path == "/v1/notifications/verify-webhook-signature":
            assert body["webhook_id"] and body["transmission_sig"] and body["webhook_event"]["id"]
            return httpx.Response(200, json={"verification_status": self.verify_result})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": path})


@pytest.fixture
def fake_paypal():
    fp = FakePayPal()
    s = get_settings()
    s.paypal_client_id, s.paypal_client_secret, s.paypal_webhook_id = "sb-client-id", "sb-secret", "WH-TEST-123"
    s.paypal_base_url = "https://api-m.sandbox.paypal.com"
    paypal_client.set_transport_for_tests(httpx.MockTransport(fp.handler))
    yield fp
    paypal_client.set_transport_for_tests(None)


WEBHOOK_HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PAYPAL-TRANSMISSION-ID": "69cd13f0-d67a-11e5-baa3-778b53f4ae55", "PAYPAL-TRANSMISSION-SIG": "c2lnbmF0dXJl",
    "PAYPAL-TRANSMISSION-TIME": "2026-11-11T10:00:00Z", "Content-Type": "application/json",
}
