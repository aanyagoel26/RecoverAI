# RecoverAI — Feature Guide

RecoverAI watches a PayPal merchant's payments and finds the ones that failed, stalled or were abandoned. It explains why each one went wrong, chooses the safest way to win the sale back, and only counts revenue as recovered when **PayPal confirms the payment**.

> **AI recommends. Rules decide. PayPal confirms.**

This guide walks through every screen, then gives step-by-step journeys you can follow to see each capability end to end. To install, see **SETUP_GUIDE.md**.

---

## Labels you'll see everywhere
| Label | Meaning |
|---|---|
| **SANDBOX** | Data that came from PayPal's Sandbox API or a verified Sandbox webhook |
| **LIVE** | Data that came from PayPal Live |
| **DEMO** | Seeded or simulated example data. It is never counted as verified revenue |
| **Rules-based** / **AI** | Whether a text was written by the deterministic rules engine or by the connected AI model |
| **Preview only** | The message was created but **not sent**, because no provider is connected |
| **Sent** | The email or SMS provider *accepted* the message. This does not mean it was delivered |
| **Delivery confirmed** | The provider confirmed delivery (SMS status callback) |
| **Link opened** | The customer opened the secure checkout link from that message |
| **Outcome uncertain** | A capture call timed out or PayPal returned a server error. The real status is unknown until RecoverAI reconciles with PayPal |

---

## Screens

### Overview (Dashboard)
- **KPIs:** total processed, revenue at risk, recovered revenue (PayPal-confirmed only), and recovery success rate.
- **Quick counters:** failed, pending, not approved, abandoned, needing customer action, and average time to recover. Each one opens the matching filtered list.
- **What needs attention:** up to four insights computed from your records, each showing its evidence and a recommended next step.
- **Charts and lists:** daily revenue at risk vs. recovered, failure categories, top-priority cases, and the live agent activity feed.
- **New accounts:** an empty workspace shows a "No payments yet" panel linking to PayPal setup and a test checkout.

### Mission Control
The live view of the agent's decisions.
- **Failure Lab:** one click creates a **real PayPal Sandbox order** and makes PayPal fail it, using PayPal's official sandbox negative-testing header. Scenarios:
  - Funding source declined
  - Buyer action required
  - High-value decline
  - Transaction refused
  - **Capture timeout (unsafe retry)**
  - Abandoned checkout

  Without PayPal credentials, the same scenarios run as labelled DEMO simulations.
- **Decision stream:** every agent decision. The **Blocked** tab lists actions the Trust Firewall stopped, with the reason.
- **Agent reasoning:** a nine-step trace:
  1. Detect
  2. Retrieve
  3. Analyse
  4. Score
  5. Choose action and PayPal rail
  6. Trust Firewall
  7. Execute or request approval
  8. Monitor
  9. Update analytics
- **Trust Firewall panel:** every check, with a live result: pass, fail, needs approval, or informational.
- **PayPal recovery rail:** which PayPal product the agent offers first (Pay Later, Venmo, PayPal Invoice or PayPal Checkout) and why.

### Payments
- **AG Grid table** of every payment attempt, with search and filters: failed, pending, abandoned, not approved, under recovery, recovered, needs attention.
- **New test checkout:** creates an order at PayPal and gives you a customer checkout link.
- **Payment detail:**
  - **AI failure analysis:** what happened, root cause, recovery probability with each factor shown, the recommended action, and its risks.
  - **Lifecycle:** every status change and the evidence behind it (PayPal API, verified webhook, or demo simulator).
  - **PayPal details:** order ID, capture ID, PayPal issue code, debug ID, and the raw response.
  - **Reconcile with PayPal:** asks PayPal for the order's real status. Use it whenever a payment shows **Outcome uncertain**.
  - **Cancel order** (with confirmation): stops all recovery for an unpaid order. Paid orders must be refunded in PayPal instead.
  - Refunds and partial refunds reported by PayPal are shown, with the net amount paid.
  - Duplicate-payment warnings.

### Recovery Queue
- **Priority queue** ranked by *expected recoverable value* (probability × amount, adjusted for age). Each case says why it is ranked where it is.
- **Views:** Open, Recovered, Unrecovered, Closed · paid, All; plus Urgent and Requires approval.
- **Case drawer:**
  - Approve, edit, or reject the agent's proposed action.
  - Choose the message tone (Professional, Friendly, Urgent but polite).
  - **Send test to me**.
  - Copy the secure checkout link.
  - The full decision trace and all messages for the case.
- **Recovery actions:**
  - Payment reminder with a secure link.
  - Ask the customer to finish a pending PayPal step.
  - Suggest another PayPal funding source.
  - **Send a PayPal invoice** (Invoicing v2).
  - **Retry capture**: only after reconciling, and only for a buyer-approved order.
  - Wait for a pending payment.
  - Notify the merchant, recommend support, or close.

### Failure Intelligence
Analysis computed from your payment records.
- **Filters:** 7, 30 or 90 days or all time; failure group; failure category; data source (Sandbox, Live, Demo).
- **Totals:** attempts, failures and failure rate, amount affected. Abandoned checkouts are counted separately from failures, and payments with an **uncertain outcome** have their own count.
- **Failure groups:** authentication/approval, customer-side, technical, merchant configuration, duplicate, unknown. Shown as a stacked trend over time.
- **Insights:** each with severity, evidence and a recommendation, for example "Card / instrument declined is your #1 failure cause — 6 of 15 failed attempts (40%)".
- **Which recovery works:** cases won, lost and open, and the win rate per failure category.
- **Failure patterns:** grouped by PayPal reason code, with count, amount, last seen, and an example payment.
- **Repeat failures:** customers and orders that failed more than once.
- **AI summary:** an optional plain-language summary from the connected AI provider. If no provider is available, it says so and why; the computed insights are unaffected.

### Revenue Simulator
Estimates how much of the current revenue at risk different strategies could recover, with confidence ranges. These are estimates and are never counted as recovered revenue.

### Customers
- Each customer's completed, failed, pending and abandoned counts, plus a consent toggle. RecoverAI never contacts a customer without consent.
- **Customer drawer** (click a name): lifetime paid, amount recovered, every payment, case and message, plus a **phone number** (E.164, validated) for SMS.

### Messages
- Every recovery message RecoverAI created, with an honest status: Preview only, Sent, Test sent, or Failed.
- **Detail view:** recipient, when the provider accepted it, delivery confirmation (SMS), **when the link was opened**, the AI or template source, and the full text.

### Analytics
- Charts for recovery over time, recovery by category, strategy and customer, plus definitions of each metric.
- **CSV export** of payments, cases and daily figures.

### AI Copilot
Ask questions in plain language, either on the full **AI Copilot** page or in the **Ask Copilot** drawer available on every page. Examples:
- "Why are my payments failing?"
- "Which actions require my approval?"
- "Why was this payment not retried?"
- "Explain TXN-2048"

How it answers:
- It answers through **read-only tools** over your own data, so numbers always come from the database, never from the model.
- Answers include **Open case** buttons.
- Anything that would contact a customer is only *prepared*. You confirm it in the case.
- Without an AI provider, Copilot answers common questions with the labelled rules engine.

### Integrations
All credentials are entered here, encrypted at rest (Fernet), and **never shown again**. You only see masked values such as `AbcSan…7890` or `••••••21zz`.
- **PayPal:**
  - **Sandbox** and **Live** credentials, stored separately.
  - Setup checklist.
  - **Active environment** switch. Switching to Live requires confirmation.
  - Your personal **webhook URL**.
  - **Test connection** (a real OAuth token request).
  - **Recent webhook events**, with verification and processing status, and replay for demo events.
- **AI provider:**
  - **Google Gemini (free tier, no card needed)**, Anthropic, or OpenAI (or any OpenAI-compatible API).
  - Choose Auto, Gemini, Anthropic, OpenAI or Off.
  - Test connection.
  - **AI usage** log: purpose, model, tokens, latency, errors. Prompts and outputs are not stored.
- **Email (Resend)** and **SMS (Twilio)**: save, test (optionally send a test message to yourself), remove.
- The shared demo workspace shows integrations **read-only**.

### Automation (Agent control centre)
Shows the agent's monitoring passes, pending approvals and scheduled actions. You can run a monitoring pass on demand.

### Audit log
An unchangeable record of every agent decision, user approval, PayPal event and system action. Filter by actor (agent, user, PayPal, system) and search.

### Settings
- **Business:** name, website, business type, industry, **time zone** (used for quiet hours), default currency, brand name and support email used in messages, default message tone.
- **Recovery policy:**
  - **Mode:** Automatic, Approval or Manual.
  - **Automation amount limit:** larger payments always need approval.
  - **Actions allowed to run automatically.**
  - Escalation time.
  - Reminder schedule, max messages per case, and minimum spacing between messages.
  - Abandonment and pending-alert thresholds.
  - **Channels** (email and SMS).
  - **Quiet hours.**
  - **Safe retry:** max capture retries and cooldown.
- **Notifications:** choose which events notify you. Options:
  - recovered payment
  - approval needed
  - firewall block
  - escalation
  - integration failure
  - possible duplicate
- **Security:** change password; **Sign out everywhere** (invalidates every session).
- **Demo** (demo workspace only): reset the demo data.

### Account
- Sign up, sign in, **email verification**, **forgot / reset password** (one-hour single-use links).
- A password reset signs out every other session.
- **Light and dark theme** (moon/sun button in the top bar).

### Customer checkout page (`/pay/<token>`)
The secure page customers reach from a recovery message.
- Shows the order and PayPal's buttons, using the JS SDK with **Pay Later** and **Venmo** enabled.
- Shows **Pay Later messaging**.
- Puts the chosen recovery rail first.
- Refuses to take a second payment if the order is already paid, pending or cancelled.

---

## How RecoverAI keeps money safe
- **AI cannot move money.** Captures only happen after the customer approves in PayPal, or as a safe retry of an order the buyer already approved.
- **Trust Firewall** before every action:
  - not already paid
  - no pending capture
  - order not cancelled or refunded
  - customer consent
  - message limit and spacing
  - amount unchanged
  - merchant approval (by mode)
  - automation amount limit and allow-list
  - quiet hours
  - idempotency
- **Timeouts are not failures.** A capture that times out or hits a PayPal 5xx is marked **Outcome uncertain**. RecoverAI never retries blindly. It reconciles with PayPal first, and retries only if PayPal still shows the order as buyer-approved and uncaptured. The retry reuses the **same idempotency key** (`PayPal-Request-Id`), so PayPal cannot charge twice. Retries are limited, spaced by a cooldown, and locked against concurrent attempts.
- **Revenue is only counted when PayPal confirms it**, through a capture response or a verified webhook. DEMO data and simulator estimates are never counted.
- **Webhooks are verified** with PayPal's signature-verification API, de-duplicated by event ID, and applied idempotently. Out-of-order events can't move a payment backwards.
- **Secrets never reach the browser** and are never logged. Saved credentials are encrypted and masked.

---

## Journeys to try

### 1. Explore the demo in two minutes
1. Landing page → **Explore the demo workspace**.
2. **Overview**: read *What needs attention*.
3. **Mission Control → Failure Lab → Funding source declined.** Watch the nine-step reasoning and the Trust Firewall.
4. Click **Open case** → choose a tone → **Approve & send**.
5. **Messages**: the message is shown as **Preview only** (no email provider in the demo), with the secure link.

### 2. Set up your own merchant account
1. **Create an account** → onboarding: business details, then **PayPal Sandbox** keys (save, then test connection), then recovery mode, then first product.
2. Verify your email. The link is in your inbox, or in the API window if email isn't connected.
3. **Settings → Business**: set your time zone and brand name.

### 3. Take a real sandbox payment
1. **Payments → New test checkout** → open the link.
2. Pay with your **sandbox buyer** in the PayPal popup.
3. The payment becomes **Completed · SANDBOX**.
4. With webhooks set up, **Integrations → Recent webhook events** shows `PAYMENT.CAPTURE.COMPLETED` with **Verify: SUCCESS**, applied once.

### 4. Recover a real declined payment
1. **Mission Control → Failure Lab → Funding source declined.** PayPal Sandbox declines a real order.
2. The agent picks the **Pay Later** rail.
3. **Approve & send** → open the checkout link → pay as the sandbox buyer.
4. The case becomes **Recovered**. *Revenue recovered* increases by the PayPal-confirmed amount.

### 5. See an unsafe retry being blocked
1. **Failure Lab → Capture timeout (unsafe retry).** PayPal answers the capture with a server error.
2. The payment shows **Outcome uncertain**. The agent proposes *Retry capture*, but only after reconciling.
3. Approve it. RecoverAI reconciles first, finds that the buyer never approved the order, and **blocks** the retry. Instead it proposes asking the customer to finish checkout.
4. **Mission Control → Blocked** lists the blocked retry with its reason.
5. On the payment page, **Reconcile now** shows what PayPal reports.

### 6. Understand why payments fail
1. **Failure Intelligence** → set the period and filters.
2. Read the insights (each has evidence), *Which recovery works*, the reason-code patterns and repeat failures.
3. Click **AI summary**. With an AI provider you get a written summary; without one, an honest "unavailable" notice.

### 7. Tune the policy and automation
1. **Settings → Recovery policy** → **Automatic** mode.
2. Set the automation amount limit (for example 250.00), allow only *Send payment reminders*, and set quiet hours of 21:00–08:00.
3. The agent now acts on its own only inside those limits.
   - Larger payments, actions that aren't allowed, or messages during quiet hours are held for approval or rescheduled.
   - You're notified according to **Settings → Notifications**.

### 8. Ask Copilot
1. **AI Copilot** → "Which actions require my approval?" → click **Open case** on an answer.
2. "Why was this payment not retried?" → Copilot explains the firewall decision from the stored trace.

### 9. Connect AI, email and SMS
1. **Integrations → AI provider**: paste a key → **Test connection** → choose the provider. Explanations and messages now show **AI**. **AI usage** shows each call.
2. **Integrations → Email**: connect Resend → send a test to yourself. Approved messages now show **Sent**.
3. **Integrations → SMS**: connect Twilio → enable SMS in **Settings → Recovery policy → Channels** → add a customer phone number. SMS messages show **Delivery confirmed** once Twilio reports delivery.

### 10. Security housekeeping
1. **Settings → Security**: change password, or **Sign out everywhere**.
2. **Sign in → Forgot password?** → open the link → set a new password. Every old session is signed out.
3. **Audit log**: every one of these actions is recorded.
