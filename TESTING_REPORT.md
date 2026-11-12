# RecoverAI — Testing Report

This report states **what was actually executed**, what the results were, and **what could not be tested** in the build environment. Nothing below is claimed as passing unless it was run.

**Date:** 12 Nov 2026
**Build environment:**
- Linux container, Python 3.13.18, Node 22.23.4
- PostgreSQL 16
- Chromium (driven by Playwright for Python)

**Network restriction:** outbound access to PayPal, Google Gemini, Anthropic, OpenAI, Resend and Twilio was **blocked** in this environment. RecoverAI's own *Test connection* reported it honestly as `NETWORK_ERROR: Could not reach PayPal: 403 Forbidden`.

---

## Summary

| Area | What was run | Result |
|---|---|---|
| Backend automated tests | `python -m pytest -q` (75 tests) | **75 passed**, 0 failed |
| Frontend type-check and build | `npm run build` (`tsc -b` + `vite build`) | **Passed**. One warning: the main JS bundle is ~2.1 MB (~600 KB gzipped) |
| Browser end-to-end | 17 journeys, Playwright + Chromium, against the real API (SQLite) and the Vite dev server | **17 / 17 passed** |
| Browser console | Errors captured across all journeys | 2, both **intentional** negative tests (wrong password → 401; invalid phone → 422) |
| Postgres migration | `supabase/migrations/0001_init.sql` applied to a fresh PostgreSQL 16 database (and `0002_gemini_provider.sql` applied to both a fresh and an older database) | **24 tables created; RLS enabled on 24 / 24** |
| App on Postgres | Demo login, overview, intelligence, insights, integrations, firewall, cases, customers, Failure Lab, vault save | **All HTTP 200**; credentials stored encrypted and returned masked |
| Real PayPal Sandbox calls | — | **Not executed** (no network to PayPal). See *Needs your credentials* |
| Real AI, email, SMS calls | — | **Not executed** (no keys, no network). See *Needs your credentials* |
| Windows | — | **Not executed** (the guide was written for Windows but run here on Linux) |

---

## 1. Backend automated tests (75)

Run with `cd backend; python -m pytest -q`. The tests use an in-memory SQLite database and **FakePayPal**, an in-process double that reproduces PayPal's documented contracts:
- OAuth token and 401 refresh
- Orders v2 create, get and capture, including `PayPal-Request-Id` replay
- 422 issues such as `INSTRUMENT_DECLINED` and `PAYER_ACTION_REQUIRED`
- 5xx `INTERNAL_SERVER_ERROR`
- Invoicing v2 create, send and get
- `verify-webhook-signature`
- the sandbox `PayPal-Mock-Response` header

| File | Tests | Covers |
|---|---|---|
| `test_paypal.py` | 18 | OAuth caching and refresh; client config never exposes the secret; connection test; full sandbox recovery end to end; a capture is never repeated; declines; pending captures wait and never charge again; webhook signature rejection, missing headers, untrusted when unconfigured; a frontend redirect is not proof of payment; a checkout can't capture another order; simulator refuses real sandbox orders; duplicate completed payment flagged; simulated webhooks labelled |
| `test_platform.py` | 22 | Signup, login, weak passwords; **tenant isolation**; analyst role can't approve; state transitions and out-of-order events; classification only from documented PayPal codes ("Unknown" never invented); no reminders for paid orders; **no contact without consent**; message limits; **preview-only email never claimed as sent**; AI recommendations validated by policy; AI message rejected if it drops the amount; revenue numbers come from rows; simulator estimates never counted; agent detects abandonment; sequential follow-ups |
| `test_agentic.py` | 8 | Failure Lab real-decline → Pay Later rail; high value → PayPal Invoice + `INVOICING.INVOICE.PAID` recovery; invoice sync; lab labelled DEMO without PayPal; abandoned → real order + Venmo rail; **negative testing refused against Live**; Trust Firewall blocks and traces; runs endpoint |
| `test_llm_path.py` | 3 | AI explanation used, but rules decide; Copilot tool loop; AI failure falls back with a label |
| `test_v3.py` | 24 | See below |

`test_v3.py` covers:
- **Credential vault:** masks and encrypts values; credentials saved in the dashboard drive real PayPal calls and webhook verification; `SECRETS_KEY` accepts any long string.
- **Live switch:** needs explicit confirmation, and Sandbox credentials are never used for Live.
- **OpenAI-compatible provider:** tool loop works and usage is recorded.
- **AI unavailable is transparent:** no fake output.
- **Google Gemini (free tier):** requests go to Google's OpenAI-compatible endpoint with the right key, model and token budget; Gemini's thought-signature metadata on tool calls is echoed back unchanged; a free-tier rate limit (HTTP 429) falls back to labelled rules-based text with a clear reason; *Auto* picks Gemini when it is the only key.
- **Timeouts and retries:**
  - a timeout is not treated as a failure;
  - an **unsafe retry is blocked** after reconciliation;
  - a **safe retry reuses the same idempotency key**;
  - **concurrent retries are locked**;
  - a capture 5xx flags `reconcile_required`.
- **Refunds and policy:** partial-refund state; Automatic mode holds large amounts for approval; settings validation; **cancelling an order blocks further recovery**.
- **Accounts:** password reset (no account enumeration, every session invalidated); email verification and sign-out-everywhere.
- **Messaging:** Failure Intelligence is evidence-based; **Twilio signature check**; click tracking.
- **Shared demo:** integrations are locked.
- **Trust Firewall display:** passive actions show a pending capture as *information*, while customer-facing actions are still blocked by it.

---

## 2. Browser end-to-end journeys (17 / 17 passed)

Real Chromium, a real API process (`uvicorn`, fresh SQLite) and the real Vite dev server. No mocks in the browser.

| # | Journey | Verified |
|---|---|---|
| J1 | Sign up → onboarding | Account created, redirected to onboarding |
| J1 | Onboarding: save PayPal Sandbox keys | Saved values come back **masked**; the secret never appears in page HTML; *Test connection* shows PayPal's real error (no fake success) |
| J1 | Finish onboarding | Dashboard reached |
| J2 | Email verification | Link taken from the API's dev log; page confirms "Email verified" |
| J3 | Integrations vault UI | Setup checklist; per-merchant webhook URL; Live switch opens a confirmation (then cancelled); AI, Email and SMS tabs render; secret absent from HTML |
| J4 | Settings tabs | Recovery policy saved and **still there after a reload** (amount 250.00); Notifications and Security tabs |
| J5 | Forgot / reset password | Reset link → new password; **old password rejected**; new password signs in |
| J6 | Demo login + dashboard | "What needs attention" insights shown |
| J6 | Failure Intelligence | Charts and tables render; **AI summary shows "unavailable" honestly** (no provider) |
| J7 | Mission Control | *Capture timeout (unsafe retry)* scenario run; **Blocked** tab shown |
| J7 | Uncertain payment | Payment shows **Outcome uncertain**; **Reconcile now** shows PayPal's (simulated) status |
| J8 | Approve a recovery action | Case drawer → *Approve & send* → message appears as **Preview only** with "Link opened: Not yet" |
| J8 | Customer drawer | Invalid phone rejected (422 shown); valid E.164 saved |
| J8 | Copilot page | "Which actions require my approval?" answered (rules mode) |
| — | Theme | Light/dark toggle flips the theme |
| — | Mobile 390 px | No horizontal overflow on Dashboard, Integrations, Settings → Policy |
| — | All 15 nav routes | Every page renders (no 404) |

Also checked directly against the API: in the demo, approving the timeout scenario's *Retry capture* gives `BLOCKED — PayPal order status is PAYER_ACTION_REQUIRED: only the buyer can approve it, so a capture cannot be retried`, and a *Request customer action* is proposed instead.

---

## 3. Defects found during this testing round (all fixed and re-tested)

| Defect | Impact | Fix | Regression test |
|---|---|---|---|
| Adding Gemini changed the allowed-provider list in the database | An existing local database would reject Gemini keys | Startup rebuilds just the credentials table in place (all data and saved keys kept); Supabase gets `0002_gemini_provider.sql` | Verified on a database created by the previous version |
| Internal actions (e.g. *Retry capture*) could run automatically in **Approval** mode | Policy bypass | The planner now respects `requires_approval` | `test_timeout_is_not_failure_and_unsafe_retry_blocked` (asserts the retry stays *Proposed*), `test_automatic_mode_holds_large_amounts_for_approval` |
| The **shared demo** workspace accepted saved API keys and a Live switch | One visitor could plant keys or break PayPal for every other visitor | Integration changes are locked on the demo (`DEMO_INTEGRATIONS_LOCKED`); UI is read-only with an explanation | `test_shared_demo_integrations_are_locked` |
| `SECRETS_KEY` had to be in Fernet format | Any other value (e.g. a platform-generated secret) would crash the vault | Any long string is accepted (SHA-256 stretched) | `test_secrets_key_accepts_any_long_string` |
| Trust Firewall showed a red ✗ on "wait for pending payment" actions that were correctly executed | A misleading trace in demos | Shown as *informational* for passive actions; still blocks customer-facing actions | `test_passive_actions_show_pending_as_information_not_failure` |
| Integrations page overflowed horizontally on phones (630 px on a 390 px screen) | Layout broken on mobile | Flex/grid children may shrink | E2E mobile check |
| Forgot-password notice could say "no email was sent" when the account had its own email connection | Misleading message | Reworded to state only what is known | — (copy change) |
| Checkout click tracking (`?c=`) broke link-token parsing | Tests failed | Token parsing fixed | existing tests |

---

## 4. Needs your credentials (not tested here)

These paths are implemented against PayPal's and the providers' documented APIs and tested against faithful doubles. They have **not** been run against the real services, because this environment could not reach them. Please run them once yourself (SETUP_GUIDE.md §7–10 and checklist §17):

| Integration | What to verify | How |
|---|---|---|
| PayPal Sandbox OAuth | Real token | Integrations → **Test connection** → *Connected* |
| PayPal Orders + JS SDK | Buttons render; buyer pays; capture response → **Completed · SANDBOX** | Payments → **New test checkout** |
| PayPal negative testing | Real `INSTRUMENT_DECLINED` from PayPal with a `debug_id` | Mission Control → Failure Lab |
| PayPal webhooks | `Verify: SUCCESS`, processed once | Register webhook (tunnel or deployed URL) and pay a test checkout |
| PayPal Invoicing v2 | Invoice created and sent; `INVOICING.INVOICE.PAID` recovers the case | Failure Lab → *High-value decline* → approve; pay the invoice as the sandbox buyer |
| PayPal Live | Not intended for the hackathon; never tested | — |
| Google Gemini / Anthropic / OpenAI | Real explanation, message, Copilot and summary; AI usage rows | Integrations → AI provider → Test, then use any AI feature |
| Resend | Real email received; verification and reset emails | Integrations → Email → Send test to yourself |
| Twilio | Real SMS; signed status callback → *Delivery confirmed* | Integrations → SMS → test; needs a public URL for callbacks |
| Windows setup | The PowerShell steps in SETUP_GUIDE.md | Follow the guide |

**What the doubles can't prove:** PayPal's real response fields for edge cases (we follow the documented shapes); sandbox-specific behaviour of the `PayPal-Mock-Response` codes; Resend/Twilio account restrictions (unverified domains, trial numbers); real AI model output quality.

---

## 5. Not covered / known limitations
- **No load or soak testing.** The rate limiter, the agent loop and the retry lock run in-process. For more than one API instance, use shared infrastructure: Redis for limits and locks, and one scheduled worker calling `POST /api/agent/run`.
- **No automated accessibility audit** (axe or similar) was run. Dialogs support Escape, buttons have labels, and colours were checked visually in both themes.
- **Mobile:** only checked for horizontal overflow on three key pages at 390 px.
- **Email delivery status:** only "accepted by Resend" is tracked. Resend delivery webhooks are not wired. SMS delivery is confirmed through Twilio's callback.
- **Refunds** are read from PayPal (full and partial) but never *issued* by RecoverAI. This is deliberate.
- **Upgrades:** a Postgres database created by an older RecoverAI version is not migrated in place. Start with a fresh database and the current migration. Old local SQLite files are backed up and recreated automatically.
- **Bundle size:** the dashboard is a single ~600 KB (gzipped) bundle. Route-level code splitting would speed up first load.

---

## How to reproduce
```powershell
# backend tests
cd backend; .\.venv\Scripts\Activate.ps1; python -m pytest -q        # 75 passed

# frontend type-check + build
cd ..\frontend; npm run build

# Postgres migration (any Postgres 14+)
psql "<DATABASE_URL>" -f ..\supabase\migrations\0001_init.sql
```
The Playwright journeys were run from a script outside the project, against `http://localhost:5173` with the API on port 8000. Each one corresponds to a journey in FEATURE_GUIDE.md, so you can repeat them by hand.
