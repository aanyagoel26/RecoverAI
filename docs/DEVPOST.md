# Devpost submission: RecoverAI

> Copy each section into the matching Devpost field. Replace `<…>` placeholders with your links.

**Project name:** RecoverAI

**Tagline (≤ 200 chars):** The trusted AI agent that turns failed PayPal payments into completed ones: it reads PayPal's own signals, picks the right PayPal product to win each sale back, and proves every decision.

**Links:** Live demo `<https://recoverai-web.onrender.com>` · Repo `<https://github.com/you/recoverai>` · Video `<YouTube URL>`

**Judge quick test:** open the live demo → **Launch live demo** → **Mission Control** → click **Funding source declined** → **Review & approve** → **Approve & send** → open the link in the message → pay with the PayPal Sandbox buyer `<sandbox buyer email / password>`.

---

## Inspiration
Roughly 7 in 10 online carts are abandoned. Baymard Institute's average across 50 studies is 70.22%. On top of that, every declined or stuck PayPal capture is revenue a merchant has already earned and is about to lose. Small merchants handle this by hand, if at all: they read cryptic error codes, guess what went wrong, and send one generic "you forgot something" email.

At Ai4 2026, PayPal said the biggest obstacle to AI adoption is **trust**. We wanted to build an agent a merchant could let loose on real payments. It acts on its own where that's safe, it shows its work, and it can never move money.

## What it does
RecoverAI is an autonomous recovery agent for PayPal merchants.

- **Reads real PayPal signals.** It classifies failures from PayPal's Orders v2 issue codes, capture states and signature-verified webhooks. When PayPal gives no reason, RecoverAI says *Unknown* instead of inventing one.
- **Routes each lost sale to the right PayPal product (recovery rails).**
  - Declined funding source → a checkout that leads with **PayPal Pay Later** plus Pay Later messaging, rather than retrying the same card.
  - US buyer who abandoned a small mobile basket → **Venmo**.
  - High-value or repeatedly failing order → a **PayPal Invoice** (Invoicing API v2) the buyer can pay from any device.
  - Everything else → a fresh, buyer-approved **PayPal Checkout**.
- **Proves every decision with a Trust Firewall.** Each agent run is recorded as a nine-step trace: detect → retrieve → analyse → score → choose action and rail → guardrails → act → monitor → analytics. Every guardrail result is shown: AI cannot move money, order not already paid, no pending capture, consent, message limit, spacing, amount unchanged, merchant approval, idempotency.
- **Explains and writes with Claude.** Plain-English root cause, personalised messages in three tones, and a Copilot that answers using **tool calls over the merchant's own data**, so it never makes up numbers.
- **Counts revenue only when PayPal confirms it.** That means the capture response or a verified `PAYMENT.CAPTURE.COMPLETED` / `INVOICING.INVOICE.PAID` webhook, never a browser redirect.
- **Failure Lab.** One click creates a real PayPal Sandbox order and asks PayPal for a genuine decline using PayPal's official negative-testing header. The agent then reacts live to PayPal's own response.
- **Works from any AI assistant.** An MCP server lets merchants ask Claude Desktop (or any MCP client) "which payments should I chase today?". Approvals stay with the human.
- Also included: autonomy modes (Automatic / Approval / Manual), a priority queue ranked by expected recoverable value, a Revenue Rescue Simulator with confidence ranges, analytics with CSV export, and a full audit log.

## How we built it
- **Backend:** Python, FastAPI, SQLAlchemy, Supabase Postgres (24 tables, row-level security deny-by-default, generated migration). Integer-cents money.
- **PayPal:**
  - OAuth client credentials (cached token, refresh on 401).
  - **Orders v2** create, get and capture, with `PayPal-Request-Id` idempotency.
  - **Invoicing v2** create, send and get.
  - **Webhooks**, verified with `/v1/notifications/verify-webhook-signature`, de-duplicated and applied idempotently.
  - **JS SDK** buttons with `enable-funding=paylater,venmo` and **Pay Later messages**.
  - **Sandbox negative testing** (`PayPal-Mock-Response`) for the Failure Lab.
- **AI:** Claude (Anthropic Messages API) for explanations, message drafting and a tool-using Copilot with 10 tools. A deterministic policy engine decides actions, rails and guardrails, and an explainable weighted model scores recovery probability. It learns from the merchant's own closed cases and is labelled as not ML. Every AI output is guarded: a drafted message must keep the exact amount and the secure link, or it falls back to a template.
- **Agentic:** MCP server (Python MCP SDK 2.x) with 8 tools over the same REST API, so the same Trust Firewall applies.
- **Frontend:** React, TypeScript, Tailwind, Recharts, **AG Grid** for the live payments table, and the official `@paypal/react-paypal-js`.
- **Deploy:** one-click **Render** Blueprint (API + static site).
- **Quality:** 72 backend tests (PayPal flows against a contract-faithful PayPal double, webhooks, idempotency, safe retry and reconciliation, credential vault, tenant isolation, guardrails, AI provider abstraction, auth flows), migration validated on Postgres 16, and 17 headless-browser end-to-end journeys (all passing; the only console errors are the two intentional negative tests).

## Challenges we ran into
- **Not making things up.** It's tempting to map every processor code to a reason. We only map documented PayPal issue codes. Anything else becomes *Unknown*, and raw codes are shown as evidence.
- **Never charging twice.** Pending captures, out-of-order webhooks, double clicks and retries all needed to be safe. We used a payment state machine that can't move backwards, one active case per order, reuse of an open PayPal order, a 409 on capture when the order is already paid, and DB-unique PayPal order, capture, invoice and event IDs.
- **Demoing failures honestly.** Real declines are hard to trigger on purpose. PayPal's negative-testing header gives a *genuine* PayPal error response, and everything simulated is labelled DEMO everywhere.

## Accomplishments that we're proud of
- An agent that acts autonomously **and** can show exactly why it did each thing and why it held back.
- Using four PayPal products as recovery rails, which turns a cost centre (failed payments) into PayPal volume.
- The same safety rules apply whether an action comes from the dashboard, the agent loop, the Copilot or an MCP client.

## What we learned
Trust is a product feature. Judges, merchants and buyers all need to see *why*. Splitting "AI explains" from "rules decide" from "PayPal confirms" made the system both safer and easier to explain.

## What's next
- PayPal's Agent Toolkit and remote MCP server, for actions inside the merchant's own PayPal account.
- A trained recovery model once there are enough labelled outcomes, keeping the same explainable interface.
- Disputes API signals, Store Sync / Agent Ready catalog context, and multi-currency.

## Built with
`paypal-orders-v2` `paypal-invoicing-v2` `paypal-webhooks` `paypal-js-sdk` `pay-later` `venmo` `claude` `anthropic` `mcp` `python` `fastapi` `sqlalchemy` `postgresql` `supabase` `react` `typescript` `tailwindcss` `ag-grid` `recharts` `render`

## Sponsor categories to select
- **Best Use of Render**: deployed with the included `render.yaml` Blueprint.
- **Best Use of AG Grid**: the Live Payments grid (sorting, column filters, pagination, CSV export).
- Main categories: Best Overall, Best Use of PayPal + AI, Best Use of Agentic Commerce, Best Demo Delivery.
