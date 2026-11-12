# Submission checklist: deadline 13 Nov 2026, 1:30 AM IST (12 Nov, 12:00 PM PST)

Submit a draft on Devpost early. You can edit it until the deadline.

## 1. PayPal Sandbox (20 min)
- [ ] developer.paypal.com → Apps & Credentials (Sandbox) → **Create App** → copy Client ID and Secret.
- [ ] Testing Tools → Sandbox Accounts → note the **Personal** buyer email and password, and the **Business** account email.
- [ ] (After deploy, step 3) App → **Webhooks → Add**. URL `https://<api>/api/webhooks/paypal`. Events:
  - `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.PENDING`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.REFUNDED`
  - `CHECKOUT.ORDER.APPROVED`, `INVOICING.INVOICE.PAID`, `INVOICING.INVOICE.CANCELLED`

  Copy the **Webhook ID**.

## 2. GitHub (10 min)
- [ ] Create a **public** repo, unzip the project, then `git init && git add . && git commit -m "RecoverAI" && git push`.
- [ ] Check that no `.env` file was committed. `.gitignore` already excludes it.

## 3. Deploy on Render (25 min, also targets the Render sponsor prize)
- [ ] render.com → **New → Blueprint** → pick the repo. `render.yaml` creates `recoverai-api` and `recoverai-web`.
- [ ] Fill in the secret env vars on `recoverai-api`: `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `GEMINI_API_KEY` (free), `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (and `PAYPAL_WEBHOOK_ID` after step 1). Optional: `RESEND_API_KEY` + `EMAIL_FROM`. These power the shared demo workspace, whose integrations are read-only for visitors.
- [ ] If Render gave different URLs than `recoverai-api.onrender.com` / `recoverai-web.onrender.com`, update `FRONTEND_URL`, `CORS_ORIGINS`, `PUBLIC_API_URL` (API) and `VITE_API_URL` (web, then redeploy).
- [ ] Free instances sleep. Open the API URL `/api/health` a minute before recording or before judging.
- [ ] The default SQLite database resets on each deploy, which is fine for a demo (it reseeds). For persistence, set `DATABASE_URL` to Supabase and run `supabase/migrations/0001_init.sql`.

## 4. Smoke test the real flow (20 min)
- [ ] Integrations → **Test connection** shows *Connected*, and the webhook is configured.
- [ ] Mission Control → **Funding source declined**. The banner should say *PayPal Sandbox responded* with a debug_id.
  - If PayPal returns a different issue (for example `ORDER_NOT_APPROVED`), that's fine. The agent analyses whatever PayPal really said.
  - Optionally expand *Use your sandbox buyer's name/email* so invoices go to your sandbox buyer.
- [ ] Approve → open the link → pay as the sandbox buyer → case **RECOVERED** → Integrations shows `PAYMENT.CAPTURE.COMPLETED · Verify: SUCCESS`.
- [ ] **High-value decline** → approve → open the PayPal invoice link → pay it in the sandbox → the case recovers via `INVOICING.INVOICE.PAID`, or use **Check payment status** in the case drawer.
- [ ] Profile → **Reset demo data** before recording.

## 5. MCP (optional, 10 min, strong for "Agentic Commerce")
- [ ] `cd mcp_server && pip install -r requirements.txt`, then add it to Claude Desktop (see `mcp_server/README.md`) with `RECOVERAI_API_URL` set to your Render API URL.

## 6. Record the video (60–90 min)
- [ ] Follow `docs/VIDEO_SCRIPT.md`. Keep it **under 3:00**. Upload to YouTube as **Public**.

## 7. Devpost (30 min)
- [ ] Paste `docs/DEVPOST.md` into the fields. Add the repo, video and live-demo links.
- [ ] Add a judge quick-test note including the sandbox buyer login, so judges can complete a payment.
- [ ] Gallery: upload `docs/screenshots/*.png`.
- [ ] Select the categories and sponsor prizes: Render, AG Grid.
- [ ] **Submit**, then reopen the page and confirm it shows as submitted.
