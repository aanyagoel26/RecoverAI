"""PayPal REST client (server-side only).

Implements, per PayPal's REST docs:
  * POST /v1/oauth2/token                         client-credentials OAuth, token cached until shortly before expires_in
  * POST /v2/checkout/orders                      create order (intent CAPTURE) with PayPal-Request-Id idempotency
  * GET  /v2/checkout/orders/{id}                 show order details (authoritative status)
  * POST /v2/checkout/orders/{id}/capture         capture an approved order, PayPal-Request-Id idempotency
  * GET  /v2/payments/captures/{id}               capture details
  * POST /v1/notifications/verify-webhook-signature   webhook authenticity

The client secret never leaves the backend.
"""
import hashlib
import threading
import time
import uuid
from dataclasses import dataclass

import httpx

from ..config import get_settings


@dataclass
class PayPalError(Exception):
    http_status: int
    name: str
    message: str
    issue: str | None = None
    debug_id: str | None = None
    details: list | None = None
    raw: dict | None = None

    def __str__(self):
        return f"PayPal {self.http_status} {self.name}{' / ' + self.issue if self.issue else ''}: {self.message}"


class PayPalNotConfigured(Exception):
    pass


class PayPalClient:
    def __init__(self, client_id: str, client_secret: str, base_url: str, transport: httpx.BaseTransport | None = None,
                 timeout: float = 20.0):
        self.client_id, self.client_secret = client_id, client_secret
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._lock = threading.Lock()

    # ---------- OAuth ----------
    def access_token(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and time.time() < self._token_expiry:
                return self._token
            if not (self.client_id and self.client_secret):
                raise PayPalNotConfigured("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set")
            try:
                r = self._http.post("/v1/oauth2/token", data={"grant_type": "client_credentials"},
                                    auth=(self.client_id, self.client_secret),
                                    headers={"Accept": "application/json"})
            except httpx.HTTPError as e:
                raise PayPalError(0, "NETWORK_ERROR", f"Could not reach PayPal: {e}")
            if r.status_code != 200:
                body = _json(r)
                raise PayPalError(r.status_code, body.get("error", "AUTHENTICATION_FAILURE"),
                                  body.get("error_description", "OAuth token request failed"), raw=body)
            body = r.json()
            self._token = body["access_token"]
            # refresh 60s before PayPal's stated expiry
            self._token_expiry = time.time() + max(int(body.get("expires_in", 300)) - 60, 30)
            return self._token

    def _request(self, method: str, path: str, *, json: dict | None = None, request_id: str | None = None,
                 prefer: str = "return=representation", extra_headers: dict | None = None) -> dict:
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self.access_token(force=attempt == 1)}",
                       "Content-Type": "application/json", "Prefer": prefer}
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            if extra_headers:
                headers.update(extra_headers)
            try:
                r = self._http.request(method, path, json=json, headers=headers)
            except httpx.HTTPError as e:
                raise PayPalError(0, "NETWORK_ERROR", f"Could not reach PayPal: {e}")
            if r.status_code == 401 and attempt == 0:
                continue  # token expired/revoked server-side: refresh once and retry
            if r.status_code >= 400:
                raise _to_error(r)
            return _json(r)
        raise PayPalError(401, "AUTHENTICATION_FAILURE", "PayPal rejected the access token")

    # ---------- Orders v2 ----------
    def create_order(self, *, amount_minor: int, currency: str, reference_id: str, custom_id: str, invoice_id: str,
                     description: str, brand_name: str | None = None, return_url: str | None = None,
                     cancel_url: str | None = None, request_id: str | None = None) -> dict:
        body: dict = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": reference_id[:256],
                "custom_id": custom_id[:127],
                "invoice_id": invoice_id[:127],
                "description": description[:127],
                "amount": {"currency_code": currency, "value": f"{amount_minor / 100:.2f}"},
            }],
        }
        ctx = {k: v for k, v in {"brand_name": brand_name, "return_url": return_url, "cancel_url": cancel_url,
                                 "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING"}.items() if v}
        body["payment_source"] = {"paypal": {"experience_context": ctx}}
        return self._request("POST", "/v2/checkout/orders", json=body, request_id=request_id or str(uuid.uuid4()))

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/v2/checkout/orders/{order_id}")

    def capture_order(self, order_id: str, request_id: str, mock_issue: str | None = None) -> dict:
        # PayPal-Request-Id makes retries of the same capture idempotent (PayPal stores keys for a limited time);
        # we additionally refuse to capture in our own DB if a completed capture exists.
        return self._request("POST", f"/v2/checkout/orders/{order_id}/capture", json={}, request_id=request_id,
                             extra_headers=self._mock(mock_issue))

    def _mock(self, issue: str | None) -> dict | None:
        """PayPal sandbox negative testing: PayPal returns a genuine error response for the given code.
        Refused outside the sandbox so it can never affect live traffic."""
        if not issue:
            return None
        if "sandbox" not in self.base_url:
            raise PayPalError(400, "NEGATIVE_TESTING_DISABLED", "Negative testing is only allowed against the PayPal sandbox")
        import json as _json
        return {"PayPal-Mock-Response": _json.dumps({"mock_application_codes": issue})}

    # ---------- Invoicing v2 ----------
    def create_invoice(self, *, invoice_number: str, reference: str, currency: str, amount_minor: int, item_name: str,
                       recipient_email: str, recipient_given: str, recipient_surname: str, business_name: str | None,
                       note: str | None, request_id: str) -> dict:
        body = {
            "detail": {"currency_code": currency, "invoice_number": invoice_number[:25], "reference": reference[:120],
                       "note": (note or "")[:4000], "payment_term": {"term_type": "DUE_ON_RECEIPT"}},
            "primary_recipients": [{"billing_info": {"name": {"given_name": recipient_given[:140], "surname": recipient_surname[:140]},
                                                     "email_address": recipient_email}}],
            "items": [{"name": item_name[:200], "quantity": "1",
                       "unit_amount": {"currency_code": currency, "value": f"{amount_minor / 100:.2f}"}}],
            "configuration": {"allow_tip": False, "partial_payment": {"allow_partial_payment": False}},
        }
        if business_name:
            body["invoicer"] = {"business_name": business_name[:300]}
        return self._request("POST", "/v2/invoicing/invoices", json=body, request_id=request_id)

    def send_invoice(self, invoice_id: str, request_id: str) -> dict:
        return self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/send",
                             json={"send_to_recipient": True, "send_to_invoicer": False}, request_id=request_id)

    def get_invoice(self, invoice_id: str) -> dict:
        return self._request("GET", f"/v2/invoicing/invoices/{invoice_id}")

    def get_capture(self, capture_id: str) -> dict:
        return self._request("GET", f"/v2/payments/captures/{capture_id}")

    # ---------- Webhooks ----------
    def verify_webhook_signature(self, *, headers: dict, webhook_id: str, event: dict) -> str:
        h = {k.lower(): v for k, v in headers.items()}
        required = ["paypal-auth-algo", "paypal-cert-url", "paypal-transmission-id", "paypal-transmission-sig",
                    "paypal-transmission-time"]
        if any(not h.get(k) for k in required):
            return "FAILURE"
        body = {
            "auth_algo": h["paypal-auth-algo"],
            "cert_url": h["paypal-cert-url"],
            "transmission_id": h["paypal-transmission-id"],
            "transmission_sig": h["paypal-transmission-sig"],
            "transmission_time": h["paypal-transmission-time"],
            "webhook_id": webhook_id,
            "webhook_event": event,
        }
        res = self._request("POST", "/v1/notifications/verify-webhook-signature", json=body)
        return res.get("verification_status", "FAILURE")


def _json(r: httpx.Response) -> dict:
    try:
        return r.json() if r.content else {}
    except ValueError:
        return {"raw": r.text[:500]}


def _to_error(r: httpx.Response) -> PayPalError:
    body = _json(r)
    details = body.get("details") or []
    issue = details[0].get("issue") if details and isinstance(details[0], dict) else None
    return PayPalError(r.status_code, body.get("name", "HTTP_ERROR"), body.get("message", r.reason_phrase),
                       issue=issue, debug_id=body.get("debug_id"), details=details, raw=body)


# ---------- helpers for reading PayPal responses ----------
def extract_capture(order_or_capture: dict) -> dict | None:
    """Return the first capture object from an order response (or the object itself if it is a capture)."""
    if order_or_capture.get("purchase_units"):
        for pu in order_or_capture["purchase_units"]:
            caps = (pu.get("payments") or {}).get("captures") or []
            if caps:
                return caps[0]
        return None
    return order_or_capture if "status" in order_or_capture and "amount" in order_or_capture else None


def invoice_id_from(res: dict) -> str | None:
    if res.get("id"):
        return res["id"]
    href = res.get("href") or next((l.get("href") for l in res.get("links", []) if l.get("rel") == "self"), None)
    return href.rstrip("/").split("/")[-1] if href else None


def invoice_payer_url(res: dict) -> str | None:
    meta = (res.get("detail") or {}).get("metadata") or {}
    if meta.get("recipient_view_url"):
        return meta["recipient_view_url"]
    if res.get("rel") == "payer-view":
        return res.get("href")
    return next((l.get("href") for l in res.get("links", []) if l.get("rel") == "payer-view"), None)


def approve_link(order: dict) -> str | None:
    for link in order.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return link.get("href")
    return None


_client: PayPalClient | None = None
_transport_override: httpx.BaseTransport | None = None


def set_transport_for_tests(transport: httpx.BaseTransport | None):
    global _transport_override, _client
    _transport_override, _client = transport, None
    _clients.clear()


_clients: dict = {}


def client_for(client_id: str, client_secret: str, base_url: str) -> PayPalClient:
    """Cached client per credential set (token cache lives on the client)."""
    key = (base_url, client_id, hashlib.sha256(client_secret.encode()).hexdigest())
    c = _clients.get(key)
    if c is None:
        c = PayPalClient(client_id, client_secret, base_url, transport=_transport_override)
        _clients[key] = c
    return c


def get_paypal() -> PayPalClient:
    global _client
    s = get_settings()
    if _client is None or _client.client_id != s.paypal_client_id:
        _client = PayPalClient(s.paypal_client_id, s.paypal_client_secret, s.paypal_base_url,
                               transport=_transport_override)
    return _client
