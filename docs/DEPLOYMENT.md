# Deployment

**Database: Supabase** · **API: Render or Railway** · **Frontend: Vercel**

## 1. Supabase
1. Create a project. Under **Project Settings → Database**, copy the connection string (session pooler, port 5432).
2. Run the migration: **SQL Editor** → paste `supabase/migrations/0001_init.sql` → Run. Or use `psql "$DATABASE_URL" -f supabase/migrations/0001_init.sql`.
   RLS is enabled on all 24 tables with no client policies, so the anon key can't read financial data. The API connects as the database owner and enforces tenant scoping itself.
3. If you change `app/models.py`, regenerate with `cd backend && python -m scripts.gen_migration`.

## 2. One-click Render Blueprint (API + dashboard)
`render.yaml` defines `recoverai-api` (Python, free plan, health check `/api/health`) and `recoverai-web` (static site with SPA rewrite).
1. Render → **New → Blueprint** → select the repo.
2. `JWT_SECRET` and `SECRETS_KEY` are generated.
   - Optionally fill in server-wide fallbacks on `recoverai-api`: `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and `PAYPAL_WEBHOOK_ID` (used by the shared demo workspace), plus `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, `RESEND_API_KEY` + `EMAIL_FROM`, and `TWILIO_*`.
   - Merchants can instead paste their own keys in **Integrations**.
   - Never change `SECRETS_KEY` after merchants have saved credentials.
3. If Render assigns different hostnames, update `FRONTEND_URL`, `CORS_ORIGINS`, `PUBLIC_API_URL` on the API and `VITE_API_URL` on the web service (then redeploy the web service).
4. `DATABASE_URL` defaults to SQLite (demo data reseeds on each deploy). Set it to Supabase for persistence.
5. In production the API refuses to start with the default JWT secret.

Alternatives: the `backend/Dockerfile` works on Railway or Fly, and `frontend/vercel.json` works on Vercel (set `VITE_API_URL`).

## 4. PayPal webhook
- **Each merchant:** register the URL shown in **Integrations → PayPal → Your webhook URL** (`https://<your-api-host>/api/webhooks/paypal/m/<merchant-id>`) and paste the Webhook ID into Integrations.
- **Server-wide credentials:** use `https://<your-api-host>/api/webhooks/paypal` with `PAYPAL_WEBHOOK_ID`.

See `docs/PAYPAL_SETUP.md`.

## Checklist
- [ ] `APP_ENV=production`, strong `JWT_SECRET`, `SECRETS_KEY` set (and kept)
- [ ] `CORS_ORIGINS` = exact frontend origin
- [ ] `FRONTEND_URL` set (recovery links point there)
- [ ] Integrations page: Test connection = Connected, webhook ID configured
- [ ] One sandbox checkout completes and the webhook shows `Verify: SUCCESS`
- [ ] For multi-instance scaling, replace the in-process rate limiter and agent loop with shared infrastructure (Redis or the platform's rate limiting; a single scheduled worker or cron calling `POST /api/agent/run`)
