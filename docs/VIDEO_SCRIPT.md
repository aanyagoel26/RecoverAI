# Demo video: 2:50 script and shot list

The rule is a **public YouTube video under 3 minutes**. Aim for 2:45–2:50. Record at 1440×900 (or 1920×1080, browser zoom 110%), dark mode, notifications off. Record each scene separately and cut them together. Talk over the footage afterwards so the timing is tight.

**Before recording:** Profile → **Reset demo data**. Make sure PayPal Sandbox is *Connected* (Integrations), with the webhook configured and `ANTHROPIC_API_KEY` set. Keep the sandbox buyer logged out in a second browser profile.

| Time | Screen | Voice-over (read naturally) |
|---|---|---|
| **0:00–0:14** | Landing page, slow scroll to the four rails | "Seven in ten online carts are abandoned, and every declined PayPal payment is revenue a merchant already earned. Today they chase it by hand, if at all. RecoverAI is an AI agent that wins those payments back, and you can trust it." |
| **0:14–0:32** | Mission Control → click **Funding source declined** → the blue *PayPal Sandbox responded* banner (HTTP 422, `INSTRUMENT_DECLINED`, debug_id) | "This is a real PayPal Sandbox order. We ask PayPal for a genuine decline using its official negative-testing header. That 422 and debug ID came from PayPal, not from us." |
| **0:32–1:02** | The nine-step trace animates; pause on step 5 and the **PayPal Pay Later** rail card, then the **Trust Firewall** | "The agent classifies the failure from PayPal's own code, scores its chance of recovery, and makes the key call. It doesn't retry the declined card. It routes the buyer to PayPal Pay Later. Then the Trust Firewall: AI can't move money, the order isn't paid, there's no pending capture, the buyer consented, and we're within limits. One check is on hold, and that's me." |
| **1:02–1:22** | **Review & approve** → switch tone to *Friendly* → show the Claude-written message → **Approve & send** | "Claude drafts a personal message with a secure link. If the AI ever drops the amount or the link, we fall back to a template. I approve, and the backend re-runs every guardrail before anything leaves." |
| **1:22–1:55** | Open the link → *Spread the cost with PayPal Pay Later* + Pay Later message + **Pay Later** button → sandbox buyer login → pay → *Payment confirmed by PayPal* | "This is the buyer. The page leads with Pay Later because that's what the agent chose. The buyer approves in PayPal's own flow, and our server captures with an idempotency key. The confirmation comes from PayPal's capture response, not the redirect." |
| **1:55–2:12** | Back in the dashboard: case **RECOVERED**, follow-ups **Cancelled**. Integrations → `PAYMENT.CAPTURE.COMPLETED · Verify: SUCCESS` | "The case flips to recovered and the follow-up reminders are cancelled automatically. PayPal's webhook arrives, gets its signature verified, and is de-duplicated, so revenue is never counted twice." |
| **2:12–2:28** | Mission Control → **High-value decline** → rail card shows **PayPal Invoice** → approve → invoice link opens on paypal.com | "For a $1,500 order, a checkout link isn't the right tool. The agent sends a real PayPal invoice through the Invoicing API instead. When it's paid, the webhook closes the case." |
| **2:28–2:42** | Claude Desktop with the RecoverAI MCP server: "Which PayPal payments should I chase today?" → ranked answer → "approve here" link. *(Fallback: in-app Copilot)* | "And merchants don't even need the dashboard. With our MCP server they can ask Claude, and it answers from real data. Approvals still stay with the human." |
| **2:42–2:50** | Overview: *Revenue recovered* card showing *PayPal-confirmed* | "RecoverAI. AI recommends, rules decide, PayPal confirms. Every recovered dollar is a real PayPal payment." |

## If something fails while recording
- **PayPal Sandbox down or not configured:** everything still works in demo mode. Say "here in demo mode" once. Everything simulated is labelled DEMO.
- **Pay Later button doesn't appear:** PayPal decides Pay Later eligibility by buyer and country. Pay with the main PayPal button and say "the buyer can still choose any PayPal funding source".
- **Webhook slow:** the case is already RECOVERED from the capture response. Show the webhook later on Integrations, or cut it.

## YouTube
Title: `RecoverAI — Trusted AI agent that recovers failed PayPal payments (PayPal AI Hackathon 2026)`. Visibility: **Public**. Paste the Devpost tagline into the description and add repo and live links.
