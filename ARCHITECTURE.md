# RecoverAI — Architecture

## 1. System overview

```
                    ┌──────────────────────────── Browser ─────────────────────────────┐
                    │  Merchant dashboard (React + TS + Vite)   Customer checkout /pay/<t> │
                    │  React Query · Recharts · AG Grid          PayPal JS SDK (Pay Later, │
                    │  light/dark theme                          Venmo, Pay Later messages)│
                    └───────────────┬─────────────────────────────────────┬────────────────┘
                     JSON + Bearer JWT (no secrets ever)          public, token-scoped
                                    ▼                                     ▼
┌───────────────────────────────────────── FastAPI backend ───────────────────────────────────────────┐
│ routers/  auth · paypal · payments · recovery · merchant · insights · integrations                   │
│                                                                                                      │
│ services/                                                                                            │
│   agent ─────► classifier · scoring · rails · policy (Trust Firewall) · retry (reconcile + safe retry)│
│     │          llm (Anthropic | OpenAI-compatible, usage log)  · copilot (read-only tools)           │
│     │          communication (email / SMS, click tracking) · invoices · intelligence · analytics     │
│     ▼                                                                                                │
│   checkout · webhooks · lab ─► paypal_client (OAuth cache, Orders v2, Invoicing v2, verify-webhook)  │
│   providers (encrypted credential vault, per-merchant config, Sandbox/Live separation)               │
│   audit · mailer                                                                                     │
│ background agent loop (tick every AGENT_LOOP_SECONDS)                                                │
└──────┬──────────────────────┬───────────────────────┬───────────────────────┬────────────────────────┘
       ▼                      ▼                       ▼                       ▼
 SQLite (dev) /         PayPal REST              Anthropic / OpenAI       Resend (email)
 Supabase Postgres      api-m(.sandbox).paypal   (optional)               Twilio (SMS, signed
 (24 tables, RLS)       ◄── PayPal webhooks                               status callbacks)
                          (verified, deduped)
```

The **frontend never holds a secret**. It receives masked credential values and PayPal's *public* client ID (needed by the JS SDK). Everything else, including PayPal secrets, AI keys, email and SMS keys, and webhook verification, lives in the backend.

---

## 2. Who decides what (hybrid AI)

| Decision | Made by | Never by |
|---|---|---|
| Payment state | `policy.TRANSITIONS` + evidence from the PayPal API or a **verified** webhook (`agent.apply_status`) | AI, frontend redirects |
| Failure category | `classifier.py`, from documented PayPal issue codes only ("Unknown" when evidence is missing) | AI guessing |
| Recovery probability | `scoring.py`: transparent weighted factors blended with the merchant's own closed-case history | AI |
| Recommended action | `policy.recommend()` | AI (its suggestions are validated against policy) |
| PayPal rail to offer first | `rails.choose_rail()` | — |
| Whether an action may run now | `policy.guardrails()` (the Trust Firewall) + recovery mode + role | AI |
| Whether a retry is safe | `retry.retry_checks()` after `retry.reconcile()` | AI, timeouts |
| Explanation text, message wording, Copilot prose, intelligence summary | LLM (optional), with output guards and a labelled rules fallback | — |
| Every number Copilot states | read-only tool results from the database | the model |

---

## 3. Data model (24 tables)

| Area | Tables |
|---|---|
| Tenancy & auth | `merchants`, `users` (bcrypt hash, `email_verified`, `token_version`), `auth_tokens` (SHA-256 hashed reset/verify tokens), `merchant_settings` |
| Integrations | `integration_credentials` (Fernet-encrypted payload + masked view + status), `paypal_connections`, `ai_usage` |
| Commerce | `customers` (consent, phone), `products`, `orders`, `checkout_sessions` |
| Payments | `payments` (status, data source, PayPal IDs, `reconcile_required`, `refunded_minor`), `payment_events` (append-only lifecycle), `webhook_events` (unique PayPal event id) |
| Recovery | `recovery_cases`, `recovery_actions` (unique `(case_id, idempotency_key)`), `ai_analyses`, `agent_runs` (9-step trace + guardrails + rail), `paypal_invoices` |
| Communication | `communication_logs` (status, provider, `sent_at`, `delivered_at`, `clicked_at`, `to_address`) |
| Insight & ops | `revenue_predictions`, `merchant_analytics`, `notifications`, `audit_logs` |

- **Money:** always integer minor units (`*_minor`). Floating-point is never used for amounts.
- **Tenant isolation:** every query is scoped by `merchant_id`. IDs belonging to another tenant return 404.
- **Supabase:** row-level security is enabled on all 24 tables with no client policies (deny-by-default for the public `anon` key). The API connects as the database owner.

---

## 4. Payment lifecycle

`agent.apply_status(payment, new_status, source, detail)` is the **only** way a payment changes state.

```
CREATED ─► APPROVED ─► AUTHORIZED ─► PENDING ─► COMPLETED ─► PARTIALLY_REFUNDED ─► REFUNDED
   │           │            │            │          (paid states)
   └───────────┴────────────┴────────────┴─► DECLINED | FAILED | VOIDED | CANCELLED | EXPIRED
```

How a status change is applied:
- `source` is one of `PAYPAL_API`, `PAYPAL_WEBHOOK`, `DEMO_SIMULATOR` or `SYSTEM`. Each change writes a `payment_events` row.
- Transitions not in `TRANSITIONS` are rejected. A late `PENDING` after `COMPLETED` is ignored.

Side effects by status:
- **DECLINED / FAILED:** opens one active case per order, then analyse, then plan.
- **PENDING:** cancels customer-facing actions, because a customer must never be asked to pay twice.
- **COMPLETED:** the order becomes PAID, sessions are CONVERTED, cases become RECOVERED (or CLOSED_ALREADY_PAID), remaining reminders are cancelled, and the merchant is notified.
- **Refund webhooks:** partial vs. full is decided by comparing the refunded amount with the capture.

**Revenue counted as recovered** requires a PayPal capture response or a verified webhook. `DEMO_SIMULATED` rows and simulator estimates are excluded.

---

## 5. The agent pipeline and Trust Firewall

Each decision is recorded in `agent_runs` as nine steps:
1. **Detect:** failure, abandonment, stuck order or monitoring pass.
2. **Retrieve:** order, session and attempts.
3. **Analyse:** classifier, plus an LLM explanation when available.
4. **Score:** probability with named factors.
5. **Choose:** action + PayPal rail (Pay Later / Venmo / PayPal Invoice / Checkout).
6. **Trust Firewall:** `policy.guardrails()`. Each check is `pass`, `fail`, `hold` (needs a human) or `info`.
7. **Execute or request approval:** depends on mode (Automatic / Approval / Manual) and the holds above.
8. **Monitor:** waits for `PAYMENT.CAPTURE.COMPLETED` / `INVOICING.INVOICE.PAID`.
9. **Update analytics.**

The Trust Firewall checks:
- AI cannot move money.
- The order is not already paid.
- There is no pending capture.
- The order is not cancelled or refunded.
- Consent, message limit and spacing.
- The amount is unchanged.
- Merchant approval.
- In Automatic mode: the automation amount limit, the action allow-list and quiet hours (in the merchant's time zone). Any of these sends the action to `hold`.
- Idempotency.

For passive actions (wait, notify merchant, investigate, close), a paid or pending order is reported as `info`. It is the reason for the action, not a violation.

**Execution re-checks everything.** When an approved action runs, the firewall is evaluated again (`_record_execution_run`). Customer-facing actions are blocked if any check fails at that moment, for example if the customer paid in the meantime.

**The agent loop (`agent.tick`)** runs every `AGENT_LOOP_SECONDS` or on demand (`POST /api/agent/run`). On each pass it:
- detects abandoned sessions and stuck CREATED/APPROVED orders;
- closes cases that were paid by any route;
- runs due actions, with sequential follow-ups and quiet-hour rescheduling;
- in Approval mode, converts due actions into proposals;
- reconciles payments with uncertain outcomes;
- escalates stale cases.

---

## 6. Safe retry (`services/retry.py`)

```
capture call ──► 2xx: COMPLETED/PENDING/DECLINED (PayPal's answer)
             └─► timeout / 0 / 5xx: payment unchanged, reconcile_required = true, HTTP 502 {uncertain: true}
                                   (a timeout is NEVER treated as a failure)

RETRY_CAPTURE (needs approval unless Automatic + allowed + under limit)
  1. acquire in-process lock per payment            → else blocked "retry already running"
  2. reconcile: GET /v2/checkout/orders/{id}        → apply PayPal's real status (or EXPIRED on 404)
  3. retry_checks: reconciled · not already paid · no pending · buyer approved (status APPROVED)
                   · retries < max_retry_attempts · cooldown elapsed · amount unchanged
                   · same idempotency key ("capture-<payment id>")
  4. all pass → capture again with the SAME PayPal-Request-Id (PayPal deduplicates; cannot double charge)
     any fail → BLOCKED with reason; if the buyer must act, propose REQUEST_CUSTOMER_ACTION instead
```

The **Blocked** tab in Mission Control (`GET /api/firewall/blocked`) lists every blocked action with its guardrail results.

---

## 7. PayPal integration (`paypal_client.py`, `checkout.py`, `webhooks.py`, `invoices.py`, `lab.py`)

**Credentials and environments**
- **Per-merchant credentials:** `providers.paypal_config(db, merchant, environment)` returns the merchant's encrypted Sandbox or Live credentials. If there are none, it falls back to the server environment variables.
- A token cache is kept per `(client_id, base_url)`, refreshed on 401.
- Sandbox (`api-m.sandbox.paypal.com`) and Live (`api-m.paypal.com`) credentials are stored separately. Switching to Live needs explicit confirmation.

**Orders v2**
- Create, get and capture, always with `PayPal-Request-Id`.
- A recent unapproved order is reused rather than duplicated.
- `ORDER_ALREADY_CAPTURED` triggers an authoritative sync instead of a retry.

**Invoicing v2**
- Create, send and get.
- Paid status arrives via the `INVOICING.INVOICE.PAID` webhook or a manual *Check payment status*.

**Webhooks**
- Each merchant has its own endpoint: `/api/webhooks/paypal/m/{merchant_id}`. The legacy global `/api/webhooks/paypal` remains.
- Processing order:
  1. Store the event; a duplicate event ID returns `duplicate`.
  2. Verify the signature with `POST /v1/notifications/verify-webhook-signature` using the merchant's webhook ID.
  3. Apply the event only if verification returns `SUCCESS`.
- An event whose resource belongs to a different merchant is rejected.

**Failure Lab**
- Creates real sandbox orders, then captures with `PayPal-Mock-Response` (negative testing).
- `_mock()` refuses any non-sandbox base URL.

---

## 8. Credential vault (`services/providers.py`)

| Provider | Fields |
|---|---|
| `paypal_sandbox` / `paypal_live` | client ID, secret, webhook ID |
| `anthropic` | API key, model |
| `openai` | API key, model, base URL |
| `resend` | API key, from address |
| `twilio` | account SID, auth token, from number |

- **Encryption:** Fernet (AES-128-CBC + HMAC-SHA256). The key comes from `SECRETS_KEY`: a Fernet key, or any string stretched with SHA-256. Without it, the key is derived from `JWT_SECRET` (development only).
- **Masking:** API responses only ever include masked values: `abcdef…wxyz` for IDs, `••••••wxyz` for secrets. Audit entries record *which fields* changed, never values.
- **Status:** each provider has a status (`CONFIGURED` after saving, then `CONNECTED` or `ERROR` after a test) plus the last test time and error. A connection is only marked `CONNECTED` after a real successful call.
- **Precedence:** dashboard credentials override environment variables for that merchant.
- **Shared demo:** the demo workspace is read-only for integrations (`DEMO_INTEGRATIONS_LOCKED`).

---

## 9. AI provider abstraction (`services/llm.py`)

`llm.chat(messages, system, tools, db, merchant_id, purpose)` returns an Anthropic-shaped response whichever provider answered:
- **Anthropic Messages API:** used directly.
- **OpenAI Chat Completions, or any compatible base URL:** requests are translated (tools, `tool_use`/`tool_result`, `max_completion_tokens` with fallback to `max_tokens`) and responses are translated back.

Behaviour:
- **Provider selection:** per merchant, one of `auto`, `anthropic`, `openai` or `none`.
- **Limits:** a timeout of `AI_TIMEOUT_SECONDS`; one retry on 429 or 5xx.
- **Logging:** every call writes an `ai_usage` row (purpose, provider, model, tokens, latency, success, error). Prompts, outputs and keys are never stored.
- **Failure handling:** if no provider is available or the call fails, it raises `LLMUnavailable`. Callers then show **labelled rules-based** output, or an explicit "unavailable" state such as the intelligence summary. RecoverAI never fabricates AI output.
- **Copilot** uses read-only tools, for example `get_pending_approvals`, `get_blocked_actions` and analytics. A customer-facing action is only *prepared* and needs confirmation in the UI.

---

## 10. Communication (`services/communication.py`)

- **Email (Resend):** `SENT` means Resend accepted the message. With no provider configured, the status is `PREVIEW_ONLY`.
- **SMS (Twilio):** sent only if SMS is enabled and the customer has an E.164 phone number and has consented.
  - A `StatusCallback` goes to `/api/webhooks/twilio/{merchant_id}`. Its `X-Twilio-Signature` is verified (HMAC-SHA1).
  - `delivered` sets `delivered_at`.
- **Click tracking:** checkout links carry `?c=<communication_id>`. The checkout page posts it on open, which sets `clicked_at` the first time.
- **Account email** (`mailer.py`): verification and reset emails. Tokens are random, single use, expire, and are stored only as SHA-256 hashes. Dev links are logged only outside production.

---

## 11. Authentication and security

- **Passwords and sessions:** bcrypt password hashes. JWTs carry `tv` (token version). *Sign out everywhere* and password reset bump the version, which invalidates every existing token.
- **Rate limits:** on auth, AI, webhook and public checkout endpoints. *Forgot password* answers identically whether or not the account exists.
- **Roles:** `owner` / `admin` can approve and change settings; `analyst` is read-only.
- **Headers:** strict CORS (`CORS_ORIGINS`) and security headers (`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store`).
- **Production startup:** the API refuses to start with the default `JWT_SECRET`.
- **Logging:** passwords, tokens, payment credentials and API keys are never logged.

---

## 12. Frontend

- **Stack:** React 18 + TypeScript + Vite + Tailwind. Colours are CSS variables, which allows a light and a dark theme.
- **Data fetching:** React Query, with polling for live views.
- **Pages:**
  - Overview, Mission Control, Payments (AG Grid) + payment detail
  - Recovery Queue + case drawer, Failure Intelligence, Revenue Simulator
  - Customers + drawer, Messages, Analytics, AI Copilot
  - Integrations, Automation, Audit log, Settings (tabs)
  - Auth pages: sign in, sign up, forgot/reset password, verify email
  - Public checkout
- **Routing:** `/api` is proxied to the backend in development. In production, `VITE_API_URL` points at the API.

---

## 13. Deployment topology

- **Render Blueprint** (`render.yaml`): a Python web service for the API (health check `/api/health`) and a static site for the dashboard (SPA rewrite). `JWT_SECRET` and `SECRETS_KEY` are generated.
- **Database:** Supabase Postgres via `DATABASE_URL`, after applying `supabase/migrations/0001_init.sql`. SQLite is the default for local runs.
- **Alternatives:** `backend/Dockerfile` (Railway, Fly) and `frontend/vercel.json` (Vercel).
- **MCP server** (`mcp_server/`): exposes recovery tools to MCP clients through the same REST API.

**Scaling notes:** the rate limiter, agent loop and retry lock are in-process. For multiple API instances, move limits and locks to Redis (or the platform's rate limiting), and run one scheduler calling `POST /api/agent/run`.
