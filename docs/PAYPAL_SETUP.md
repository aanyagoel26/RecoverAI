# PayPal Sandbox setup

RecoverAI uses PayPal **Orders v2** (intent `CAPTURE`), the **OAuth 2.0 client-credentials** flow, and **Webhooks** with server-side signature verification. All calls go from the backend to `PAYPAL_BASE_URL`. The client secret never reaches the browser; the client ID is public by design and is served to the checkout page for the JS SDK.

## 1. Create a sandbox app
1. Sign in at https://developer.paypal.com → **Apps & Credentials** → make sure the **Sandbox** toggle is on.
2. **Create App** (type: Merchant). Copy the **Client ID** and **Secret**.
3. In RecoverAI (your own account), open **Integrations → PayPal → PayPal Sandbox**, paste both and click **Save securely**. They are encrypted, and only masked values are shown again.
   - *Server-wide alternative:* set `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and `PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com` in `backend/.env` and restart. These are used by the demo workspace and by merchants without saved keys.
4. Click **Test connection**. This performs a real `POST /v1/oauth2/token`, and the status should read *Connected*.

## 2. Get a sandbox buyer
**Testing Tools → Sandbox Accounts.** PayPal creates a *Personal* (buyer) account. Use its email/password on the PayPal popup during checkout. Never use real credentials in sandbox.

## 3. Configure webhooks
Webhooks need a public HTTPS URL.
- Deployed: `https://<your-api>/api/webhooks/paypal/m/<merchant-id>`. Copy it from **Integrations → PayPal → Your webhook URL**. For server-wide credentials use `/api/webhooks/paypal`.
- Local: expose port 8000 with a tunnel, e.g. `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`, and set `PUBLIC_API_URL` to the tunnel URL.

In your app → **Webhooks → Add Webhook**, paste the URL and subscribe to:
`PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.PENDING`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.REFUNDED` (partial refunds are recognised), `CHECKOUT.ORDER.APPROVED`, `INVOICING.INVOICE.PAID`, `INVOICING.INVOICE.CANCELLED`.

Copy the **Webhook ID** into **Integrations → PayPal Sandbox → Webhook ID** (or `PAYPAL_WEBHOOK_ID` for server-wide credentials).

How events are handled (`backend/app/services/webhooks.py`):
1. Stored in `webhook_events` (unique on PayPal event id). A redelivery of a processed event returns `duplicate` with no side effects.
2. Verified with `POST /v1/notifications/verify-webhook-signature`, using the five `PAYPAL-*` transmission headers, `webhook_id`, and the event body.
3. Only `verification_status == SUCCESS` events change state. Others are stored as FAILURE/IGNORED and answered with HTTP 400. If `PAYPAL_WEBHOOK_ID` is missing, events are stored as `SKIPPED_NOT_CONFIGURED` and never trusted.
4. State is applied through the policy engine, so an out-of-order `PENDING` after `COMPLETED` is ignored.

> Note: events sent with the Developer Dashboard's *Webhooks simulator* are mock events. PayPal's verification endpoint may not return `SUCCESS` for them. To exercise verification end to end, use a real sandbox checkout, which emits genuine events.

## 4. Run a real sandbox recovery
1. Sign in to the demo workspace. Open **TXN-2048** → **Run AI analysis** → **Open case** → **Approve & send**.
2. Copy the secure checkout link from the message (or click *Secure checkout link*).
3. On the `/pay/<token>` page click the **PayPal** button and log in as your sandbox buyer.
4. On approval, the backend calls `POST /v2/checkout/orders/{id}/capture` (with `PayPal-Request-Id: capture-<payment id>`). The **capture response** sets the payment to COMPLETED, then the case becomes RECOVERED and its remaining reminders are cancelled.
5. Shortly after, PayPal sends `PAYMENT.CAPTURE.COMPLETED`. It is verified and applied idempotently, so recovered revenue doesn't change twice. You'll see it under **Integrations → Recent webhook events**.

The recovered payment is labelled **SANDBOX** (it is a real sandbox capture), even though the demo order it recovers is seed data.

You can also click **New test checkout** (Payments or Integrations) to create a fresh sandbox order and link.

## Duplicate-payment protection
- `PayPal-Request-Id` on create and capture (PayPal-side idempotency).
- A recent unapproved PayPal order for the same order is **reused** rather than duplicated.
- DB unique constraints on `paypal_order_id`, `paypal_capture_id`, `webhook_events.paypal_event_id`, and `(case_id, idempotency_key)` for agent actions.
- Capture is refused (HTTP 409) if the order already has a completed payment. New orders are refused while one is pending.
- `ORDER_ALREADY_CAPTURED` triggers an authoritative `GET /v2/checkout/orders/{id}` sync instead of a retry.
- A second completed payment on one order raises a *Possible duplicate payment* notification.

## Declines
If capture returns `422 INSTRUMENT_DECLINED`, the checkout page calls `actions.restart()` (PayPal's recommended handling) so the buyer can pick another funding source in the same order. RecoverAI records the issue and never retries a declined charge by itself.

## Timeouts and safe retry
- If a capture call times out, or PayPal answers with a 5xx, RecoverAI does **not** mark the payment failed. It sets *Outcome uncertain* (`reconcile_required`).
- **Reconcile with PayPal** (payment page, or automatically in the agent loop) calls `GET /v2/checkout/orders/{id}` and applies PayPal's real status.
- A *Retry capture* runs only if PayPal still shows the order **APPROVED and uncaptured**, within the retry limit and after the cooldown.
- The retry reuses the **same `PayPal-Request-Id`**, so PayPal returns the original result instead of charging twice.
- Otherwise it is blocked with the reason. If the buyer must act, a customer-action request is proposed instead.

## Failure Lab (real sandbox declines)
Mission Control → **Failure Lab** creates a real order with `POST /v2/checkout/orders`. It then calls `POST /v2/checkout/orders/{id}/capture` with PayPal's sandbox negative-testing header:
```
PayPal-Mock-Response: {"mock_application_codes": "INSTRUMENT_DECLINED"}
```
PayPal returns a genuine error response (HTTP 422, issue, debug_id). RecoverAI stores it verbatim under `raw_response.negative_testing`, and the agent analyses it like any production failure.
- If PayPal returns a different issue than the one requested, the agent uses what PayPal actually returned.
- The client refuses to send this header to any non-sandbox base URL.
- Scenarios: `card_declined` (→ Pay Later rail), `payer_action`, `high_value` (→ PayPal Invoice rail), `refused`, `timeout` (capture answered with `INTERNAL_SERVER_ERROR` → outcome *uncertain*, so reconcile before any retry), and `abandoned` (a real PayPal order that is never approved → Venmo rail for US buyers).

## Recovery rails
| Situation | Rail | PayPal product |
|---|---|---|
| Order ≥ $1,000 or ≥ 3 failed attempts | PayPal Invoice | Invoicing v2: `POST /v2/invoicing/invoices` → `POST /v2/invoicing/invoices/{id}/send`. Paid via `INVOICING.INVOICE.PAID` or `GET /v2/invoicing/invoices/{id}` (**Check payment status**) |
| Declined funding source or insufficient funds | Pay Later | Checkout link `?rail=paylater`: Pay Later messages + a `fundingSource="paylater"` button first |
| US buyer, < $300, abandoned or unapproved | Venmo | Checkout link `?rail=venmo`: a `fundingSource="venmo"` button first |
| Anything else | PayPal Checkout | Standard buttons (`enable-funding=paylater,venmo`) |

PayPal always decides Pay Later and Venmo eligibility, so RecoverAI only chooses what to *offer first*. Invoices are sent to the customer's email. To complete one in the sandbox, use **Use your sandbox buyer's name/email** in the Failure Lab so the invoice goes to your sandbox personal account.
