# RecoverAI — Setup Guide (Windows first)

This guide takes you from the ZIP file to a running RecoverAI with real PayPal Sandbox payments. Commands are for **Windows PowerShell**. macOS/Linux equivalents are given where they differ.

**Time needed:**
- About 15 minutes to run it locally in demo mode.
- About 30 more minutes to connect PayPal Sandbox, an AI provider and email.

**What works without any accounts:**
- The whole app runs locally in a clearly labelled demo mode.
- AI explanations come from a labelled rules engine.
- Customer messages are created but stored as *preview only*, and are never claimed as sent.

Every external integration is optional and can be added later from the **Integrations** page.

---

## Contents
1. [Install the software](#1-install-the-software)
2. [Extract the project](#2-extract-the-project)
3. [Set up and start the backend](#3-set-up-and-start-the-backend)
4. [Set up and start the frontend](#4-set-up-and-start-the-frontend)
5. [First login](#5-first-login)
6. [Database options](#6-database-options)
7. [Connect PayPal Sandbox](#7-connect-paypal-sandbox)
8. [Connect an AI provider](#8-connect-an-ai-provider)
9. [Connect email (Resend)](#9-connect-email-resend)
10. [Connect SMS (Twilio)](#10-connect-sms-twilio)
11. [Environment variables reference](#11-environment-variables-reference)
12. [All commands in one place](#12-all-commands-in-one-place)
13. [Run the tests](#13-run-the-tests)
14. [Optional: MCP server](#14-optional-mcp-server)
15. [Deploying](#15-deploying)
16. [Troubleshooting](#16-troubleshooting)
17. [First-run checklist](#17-first-run-checklist)

---

## 1. Install the software

| Software | Version | Why | Download |
|---|---|---|---|
| Python | **3.12** (3.11–3.13 work) | Backend API | https://www.python.org/downloads/windows/ |
| Node.js | **22 LTS** (20+ works) | Frontend dashboard | https://nodejs.org/ |
| VS Code | any | Editing `.env` and running terminals (optional) | https://code.visualstudio.com/ |
| cloudflared | any | Only needed to receive PayPal webhooks on your own PC | see [7.4](#74-webhooks-so-paypal-can-notify-recoverai) |

**Python installer:** on the first screen, tick **"Add python.exe to PATH"** and then click *Install Now*.

**Or install everything from PowerShell** (Windows 10/11 have `winget`):
```powershell
winget install -e --id Python.Python.3.12
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id Microsoft.VisualStudioCode
```

**Close PowerShell and open a new window**, so the PATH changes take effect. Then check the versions:
```powershell
python --version     # Python 3.12.x
node --version       # v22.x (or v20.x)
npm --version        # 10.x
```
If `python` opens the Microsoft Store, see [Troubleshooting → python opens the Store](#python-opens-the-microsoft-store-or-is-not-recognized).

---

## 2. Extract the project

Put the ZIP somewhere **without spaces or OneDrive syncing** in the path. For example `C:\dev`.
```powershell
New-Item -ItemType Directory -Force C:\dev | Out-Null
Expand-Archive -Path "$HOME\Downloads\recoverai.zip" -DestinationPath C:\dev -Force
cd C:\dev\recoverai
Get-ChildItem          # you should see: backend, frontend, mcp_server, supabase, docs, README.md, SETUP_GUIDE.md ...
```

The ZIP has **no** `node_modules`, no virtual environment, no database and no secrets. You create those in the next steps.

---

## 3. Set up and start the backend

Open **PowerShell window #1** (it will keep running the API).

### 3.1 Create a virtual environment and install packages
```powershell
cd C:\dev\recoverai\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
Your prompt now starts with `(.venv)`.

If you see *"running scripts is disabled on this system"*, run the line below once, answer **Y**, then activate again:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Then install the packages:
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3.2 Create your `.env`
```powershell
Copy-Item .env.example .env
```

Generate two random secrets. Run this twice and copy each output:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Open the file:
```powershell
notepad .env        # or: code .env
```

Set at least these lines, then save:
```
JWT_SECRET=<paste the first random value>
SECRETS_KEY=<paste the second random value>
```

Leave everything else as it is for now. Nothing else is required to run locally.

> `SECRETS_KEY` encrypts the API keys you later paste into the Integrations page. Keep the same value from then on: if you change it, saved credentials can't be decrypted and must be entered again.

### 3.3 Start the API
```powershell
python -m uvicorn app.main:app --reload --port 8000
```

Wait for `Application startup complete.` Leave this window open.

**Check it works** by opening these in a browser:
- http://localhost:8000/api/health → `{"ok":true,...}`
- http://localhost:8000/docs → interactive API documentation

On first start the API creates `backend\recoverai.db` (SQLite) and a labelled demo workspace.

---

## 4. Set up and start the frontend

Open **PowerShell window #2**.
```powershell
cd C:\dev\recoverai\frontend
npm install          # first time only; takes 1–3 minutes
npm run dev
```

Open **http://localhost:5173**.

The frontend needs **no** secrets. In development it forwards `/api/...` to `http://localhost:8000` automatically. `frontend\.env.example` holds only `VITE_API_URL`, and you only set it when the API runs on a different host (for example after deploying).

If `npm` is blocked by the execution policy, use `npm.cmd install` and `npm.cmd run dev`, or run the `Set-ExecutionPolicy` command from step 3.1.

---

## 5. First login

You have two choices.

**Option A: Explore the demo**
- Click **Explore the demo workspace**, or sign in as `demo@recoverai.dev` / `RecoverAI-demo-2026`.
- It contains seeded example data, labelled **DEMO** everywhere.
- It is shared, so its integrations are read-only.

**Option B: Your own account (needed to connect your own PayPal, AI, email and SMS)**
1. Click **Create an account**.
2. Enter your name, business and email, and a password of at least 10 characters.
3. The onboarding wizard asks for your business, PayPal Sandbox keys (you can skip this), recovery mode and first product.
4. **Email verification:** a banner asks you to verify your email.
   - Without an email provider, nothing is emailed. Instead the backend window prints a line like:
     `Email provider not configured. DEV link for you@example.com: http://localhost:5173/verify-email?token=...`
   - Open that link to verify.
   - Once Resend is connected (step 9), real emails are sent instead.
   - The same applies to **Forgot password** links.

> Dev links are printed only when `APP_ENV` is not `production`. In production nothing sensitive is logged.

---

## 6. Database options

### 6.1 SQLite (default — nothing to do)
`DATABASE_URL=sqlite:///./recoverai.db` stores everything in `backend\recoverai.db`.
- **Reset everything:** stop the API, delete the file, start the API again.
- **Automatic backup on upgrade:** if a `recoverai.db` from an older RecoverAI version is found, the API renames it to `recoverai.db.bak-<timestamp>` and creates a fresh one, so you never run on a broken schema.

### 6.2 Supabase Postgres (for deployment or persistence)
1. Create a project at https://supabase.com.
2. In **Project Settings → Database → Connection string**, use the *Session pooler* URI. It looks like:
   `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`
3. Open **SQL Editor**, paste the whole file `supabase\migrations\0001_init.sql`, and click **Run**.
   - This creates the 24 tables.
   - Row-level security is on for all 24 with no public policies, so Supabase's public `anon` key cannot read payment data. The API connects as the database owner and enforces per-merchant isolation itself.
4. In `backend\.env`, set `DATABASE_URL=<that URI>` (plain `postgresql://` works) and restart the API.
5. If you change `backend\app\models.py`, regenerate the migration:
   ```powershell
   cd C:\dev\recoverai\backend
   python -m scripts.gen_migration
   ```

Any other Postgres 14+ works the same way, with `DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME`.

---

## 7. Connect PayPal Sandbox

Sandbox uses fake money and test accounts, so it's safe to try anything.

### 7.1 Create the sandbox app
1. Sign in at https://developer.paypal.com with any PayPal account.
2. Go to **Apps & Credentials** and make sure the toggle at the top says **Sandbox**.
3. Click **Create App**. Give it a name (for example *RecoverAI*) and choose type **Merchant**.
4. Copy the **Client ID**, then click **Show** under Secret and copy the **Secret**.

### 7.2 Paste the keys into RecoverAI
1. Sign in to **your own account** (not the demo).
2. Open **Integrations → PayPal**.
3. Paste the Client ID and Secret into **PayPal Sandbox** and click **Save securely**.
4. Click **Test connection**. RecoverAI requests a real OAuth token from `api-m.sandbox.paypal.com`, and the status changes to **Connected**.
   - If it fails, the error PayPal returned is shown. RecoverAI never shows "Connected" without a real token.

After saving, you only ever see masked values (for example `AbcSan…7890`, `••••••21zz`). The secret is encrypted in the database and is never sent to the browser.

*Alternative (server-wide):* put `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and `PAYPAL_WEBHOOK_ID` in `backend\.env`.
- These are used by the demo workspace and by any merchant who hasn't saved their own keys.
- Keys saved in the app always win for that merchant.

### 7.3 Get a sandbox buyer (to pay test orders)
1. In the PayPal developer site, go to **Testing Tools → Sandbox Accounts**.
2. PayPal already created a **Personal** account. Open it to see its email and password, or create a new Personal account.
3. You log in with this account in the PayPal popup when paying a RecoverAI checkout link.
4. Optionally, in Mission Control → Failure Lab, open **"Use your sandbox buyer's name/email"** and enter it. Invoices and messages then go to that sandbox buyer.

### 7.4 Webhooks (so PayPal can notify RecoverAI)
RecoverAI already learns payment results from PayPal's API responses. Webhooks add PayPal's own confirmations for:
- captures that complete later (pending → completed),
- refunds,
- paid invoices.

PayPal can only call a **public HTTPS** URL, so on your own PC you need a tunnel.

**Install and start a tunnel** (PowerShell window #3):
```powershell
winget install -e --id Cloudflare.cloudflared
# close and reopen PowerShell, then:
cloudflared tunnel --url http://localhost:8000
```
It prints a URL like `https://random-words.trycloudflare.com`. Keep this window open.

**Tell RecoverAI its public URL:**
1. In `backend\.env`, set `PUBLIC_API_URL=https://random-words.trycloudflare.com`.
2. Restart the API (press Ctrl+C in window #1, then run the uvicorn command again).
3. Open **Integrations → PayPal → Your webhook URL**. It now reads:
   `https://random-words.trycloudflare.com/api/webhooks/paypal/m/<your-merchant-id>`
4. Click the copy button.

**Register it at PayPal:**
1. In your PayPal app, open **Webhooks → Add Webhook** and paste the URL.
2. Subscribe to: `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.PENDING`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.REFUNDED`, `CHECKOUT.ORDER.APPROVED`, `INVOICING.INVOICE.PAID`, `INVOICING.INVOICE.CANCELLED`.
3. Save. Copy the **Webhook ID** PayPal shows.
4. In RecoverAI **Integrations → PayPal Sandbox**, paste it into **Webhook ID**, then click **Save securely** (leave the other fields empty to keep them).

**What RecoverAI does with each event:**
- Every event is checked with PayPal's `verify-webhook-signature` API. Only `SUCCESS` changes anything.
- Duplicates are recognised by event ID and ignored.
- Events appear under **Integrations → Recent webhook events**.

> A quick `trycloudflare.com` tunnel gets a **new URL every time it starts**. When it changes, update `PUBLIC_API_URL` and the webhook URL at PayPal. You can use `ngrok http 8000` instead if you prefer.

### 7.5 Try a real sandbox payment
1. Go to **Payments → New test checkout**. This creates an order at PayPal and opens a RecoverAI checkout link.
2. Click the PayPal button and log in as your **sandbox buyer**. Approve.
3. RecoverAI captures the payment through PayPal.
4. The payment becomes **Completed** and is labelled **SANDBOX**. It counts as recovered revenue only after PayPal's capture response, or a verified webhook, confirms it.

**To see a failure and a recovery:**
1. Open **Mission Control → Failure Lab** and click **Funding source declined**. RecoverAI creates a real sandbox order and makes PayPal decline it, using PayPal's sandbox negative-testing header.
2. The agent explains the decline, picks a recovery rail and asks for approval.
3. Open the case, click **Approve & send**, open the checkout link and pay as the sandbox buyer. The case becomes **Recovered**.

### 7.6 PayPal Live (real money; only when you are ready)
- Create **Live** credentials in the developer site (Live toggle). In **Integrations → PayPal Live**, paste them and add a Live webhook.
- Switch **Active environment** to **Live**. RecoverAI asks for explicit confirmation.
- Sandbox and Live credentials are stored separately and never mixed.
- The Failure Lab and negative testing refuse to run against Live.
- Keep **Settings → Recovery policy → Max amount for automatic actions** conservative.

---

## 8. Connect an AI provider

Optional. The AI writes plain-English explanations, customer messages, Copilot answers and the Failure Intelligence summary.

The AI **never** decides payment status, eligibility or amounts. RecoverAI's rules engine and the Trust Firewall do. Without AI, screens show labelled **rules-based** text, and the AI summary says it is unavailable. RecoverAI never invents AI output.

**Anthropic (Claude)**
1. Create a key at https://console.anthropic.com → **API Keys**. Add billing credit.
2. In **Integrations → AI provider → Anthropic**, paste the key.
   - Leave **Model** empty to use the default (`claude-sonnet-5-5`), or enter any current model ID from Anthropic's docs.
3. Click **Save securely**, then **Test connection**.

**OpenAI (or any OpenAI-compatible API)**
1. Create a key at https://platform.openai.com → **API keys**.
2. In **Integrations → AI provider → OpenAI**, paste the key.
   - Model defaults to `gpt-4o-mini`.
   - **Base URL** defaults to `https://api.openai.com/v1`. Change it only for a compatible provider.
3. Save, then Test.

**Choose which one is used** with the provider selector:
- **Auto:** Anthropic if available, otherwise OpenAI.
- **Anthropic** or **OpenAI:** that provider only.
- **Off:** no AI.

**AI usage** lists every call: purpose, model, tokens, latency and errors. Prompts, outputs and keys are never stored there.

Calls time out after `AI_TIMEOUT_SECONDS` (default 45). Rate-limit and server errors are retried once.

---

## 9. Connect email (Resend)

Optional. This enables real delivery of recovery emails, email verification and password resets.
1. Create an account at https://resend.com.
2. Go to **Domains → Add Domain** and add the DNS records it shows at your domain registrar. Wait until the domain shows *Verified*.
   - Without a verified domain, Resend only lets you send to your own address.
3. Go to **API Keys → Create API Key** and copy it.
4. In RecoverAI **Integrations → Email**:
   - Paste the API key.
   - Set **From address**, for example `Store <billing@yourdomain.com>`, using the verified domain.
   - Click **Save securely**.
5. Type your own email in **Send test to** and click **Test connection**. You should receive the email.

**How status is reported:**
- **Sent** means Resend *accepted* the message. RecoverAI does not claim the email was delivered or read.
- Link clicks are tracked: when the customer opens the checkout link, the message shows **Link opened**.

The server-wide alternative is `RESEND_API_KEY` and `EMAIL_FROM` in `backend\.env`. Verification and password-reset emails use the account's own Resend connection when one is saved, and otherwise the server-wide key.

---

## 10. Connect SMS (Twilio)

Optional second channel. It is used only for customers who have a phone number and have consented to messages.
1. Create an account at https://www.twilio.com.
2. In the Console, copy the **Account SID** and **Auth Token**, then buy or claim a phone number.
   - Trial accounts can only text numbers you have verified in Twilio.
3. In RecoverAI **Integrations → SMS**:
   - Paste the SID, Auth Token and **From number** (E.164, for example `+15551234567`).
   - Click **Save securely**.
   - Optionally enter your own number in **Send test to** and click **Test connection**.
4. Turn SMS on under **Settings → Recovery policy → Channels → SMS**.
5. Add phone numbers to customers: **Customers → click a name → Phone**, in E.164 format.

**Delivery confirmation:**
- Twilio calls back `PUBLIC_API_URL/api/webhooks/twilio/<merchant-id>`. Locally, that needs the tunnel from 7.4.
- The callback's signature is verified.
- A message shows **Delivery confirmed** only when Twilio reports it delivered.

---

## 11. Environment variables reference

All of these live in **`backend\.env`** and are **backend-only**. Never copy them into the frontend.

The only frontend variable is `VITE_API_URL`, which is the public URL of the API and not a secret.

Merchant credentials (PayPal, AI, email, SMS) are best entered in the app's **Integrations** page. The env versions below are server-wide fallbacks.

| Variable | Purpose | Required? | Where to get it | Environment | Exposure |
|---|---|---|---|---|---|
| `APP_ENV` | `development` or `production`. Production refuses a default `JWT_SECRET` and never logs dev links | Yes | — | both | backend-only |
| `JWT_SECRET` | Signs login sessions | **Yes** | Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` | both (different per env) | backend-only, secret |
| `SECRETS_KEY` | Encrypts credentials saved in Integrations | Recommended (required in production) | Generate the same way; never change it afterwards | both | backend-only, secret |
| `DATABASE_URL` | Database connection | Yes (default SQLite) | Supabase → Database → Connection string | both | backend-only, secret |
| `FRONTEND_URL` | Base for checkout, verify and reset links | Yes | Your dashboard URL | both | backend-only |
| `PUBLIC_API_URL` | Public API URL; builds webhook and Twilio callback URLs | Yes | Your API URL or tunnel URL | both | backend-only |
| `CORS_ORIGINS` | Allowed browser origins (comma separated) | Yes | Your dashboard URL | both | backend-only |
| `PAYPAL_CLIENT_ID` | Server-wide PayPal app ID (fallback) | No | developer.paypal.com → Apps & Credentials | both | backend-only (the client ID itself is public by PayPal's design) |
| `PAYPAL_CLIENT_SECRET` | Server-wide PayPal secret (fallback) | No | same place → Show | both | **backend-only, secret** |
| `PAYPAL_BASE_URL` | `https://api-m.sandbox.paypal.com` or `https://api-m.paypal.com` | No | — | both | backend-only |
| `PAYPAL_WEBHOOK_ID` | Verifies server-wide webhook signatures | No (needed for fallback webhooks) | PayPal app → Webhooks | both | backend-only |
| `PAYPAL_CURRENCY` | Default currency | No (USD) | — | both | backend-only |
| `AI_PROVIDER` | `auto`, `anthropic` or `openai` | No | — | both | backend-only |
| `ANTHROPIC_API_KEY` | Server-wide Claude key | No | console.anthropic.com | both | **backend-only, secret** |
| `ANTHROPIC_MODEL` | Claude model ID | No | Anthropic docs | both | backend-only |
| `OPENAI_API_KEY` | Server-wide OpenAI key | No | platform.openai.com | both | **backend-only, secret** |
| `OPENAI_MODEL` | OpenAI model | No | OpenAI docs | both | backend-only |
| `OPENAI_BASE_URL` | OpenAI-compatible endpoint | No | Your provider | both | backend-only |
| `AI_TIMEOUT_SECONDS` | AI request timeout | No (45) | — | both | backend-only |
| `RESEND_API_KEY` | Email delivery, including verification and reset emails | No | resend.com → API Keys | both | **backend-only, secret** |
| `EMAIL_FROM` | Sender address (verified domain) | With Resend | Your domain | both | backend-only |
| `TWILIO_ACCOUNT_SID` | SMS account | No | Twilio Console | both | backend-only |
| `TWILIO_AUTH_TOKEN` | SMS auth and callback signature check | No | Twilio Console | both | **backend-only, secret** |
| `TWILIO_FROM_NUMBER` | Sending number (E.164) | With Twilio | Twilio Console | both | backend-only |
| `AGENT_LOOP_SECONDS` | How often the agent checks for work (0 = off) | No (example uses 60) | — | both | backend-only |
| `SEED_DEMO_ON_START` | Create the demo workspace | No (true) | — | dev / demo deployments | backend-only |
| `DEMO_INTEGRATIONS_LOCKED` | Keep the shared demo's integrations read-only | No (true) | — | both | backend-only |
| `VITE_API_URL` | Where the dashboard finds the API (empty locally) | Only when deployed | Your API URL | production | **frontend (public, not a secret)** |

---

## 12. All commands in one place

```powershell
# ---- one-time setup ----
cd C:\dev\recoverai\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into JWT_SECRET
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into SECRETS_KEY
notepad .env

cd ..\frontend
npm install

# ---- every time: window 1 (API) ----
cd C:\dev\recoverai\backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --port 8000

# ---- every time: window 2 (dashboard) ----
cd C:\dev\recoverai\frontend
npm run dev                      # open http://localhost:5173

# ---- optional: window 3 (webhooks to your PC) ----
cloudflared tunnel --url http://localhost:8000
```

**macOS/Linux** differences:
- Activate with `source .venv/bin/activate`.
- Copy with `cp .env.example .env`.
- Use `python3` where `python` is not found.

---

## 13. Run the tests

```powershell
cd C:\dev\recoverai\backend
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

Expected: `72 passed`. The tests use an in-memory database and a built-in PayPal double, so they need no internet access and no keys.

Type-check and build the frontend:
```powershell
cd C:\dev\recoverai\frontend
npm run build
```

What is and isn't covered is in **TESTING_REPORT.md**.

---

## 14. Optional: MCP server

Lets Claude Desktop (or any MCP client) query your recovery data and draft actions. Approvals still happen in the dashboard.
```powershell
cd C:\dev\recoverai\mcp_server
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:RECOVERAI_API_URL = "http://localhost:8000"
python server.py
```
See `mcp_server\README.md` for the Claude Desktop config. Omit `RECOVERAI_EMAIL` and `RECOVERAI_PASSWORD` to use the demo workspace.

---

## 15. Deploying

See `docs\DEPLOYMENT.md`. In short: Render → **New → Blueprint** → select the repo. `render.yaml` creates the API and the static dashboard, and generates `JWT_SECRET` and `SECRETS_KEY`.

After deploying:
- Set `DATABASE_URL` to Supabase.
- Check that `FRONTEND_URL`, `CORS_ORIGINS`, `PUBLIC_API_URL` and `VITE_API_URL` match the real hostnames.
- Register the PayPal webhook with the deployed URL. No tunnel is needed.

---

## 16. Troubleshooting

#### `python` opens the Microsoft Store or is "not recognized"
- Reinstall Python with **Add python.exe to PATH** ticked.
- Or turn off *Settings → Apps → Advanced app settings → App execution aliases → python.exe / python3.exe*.
- Then open a **new** PowerShell window. `py -3.12` also works in place of `python`.

#### "running scripts is disabled on this system"
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

#### `pip install` fails building a package
- You are probably on a Python version without prebuilt wheels. Use Python 3.12.
- Delete the `.venv` folder and recreate it with `py -3.12 -m venv .venv`.

#### Port 8000 or 5173 is already in use
```powershell
Get-NetTCPConnection -LocalPort 8000 | Select-Object OwningProcess
Stop-Process -Id <that number>
```
Or run the API on another port (`--port 8001`). If you do, change the proxy target in `frontend\vite.config.ts` to match.

#### The dashboard loads but pages say "Cannot reach the RecoverAI API" or "Request failed (500/502)"
- The API isn't running, or it isn't on port 8000. The dev server forwards `/api` to port 8000, and reports 500/502 when nothing answers there. Check window #1, then http://localhost:8000/api/health.

#### Login works, then I'm signed out immediately
- `JWT_SECRET` changed while you were signed in, or you used **Sign out everywhere**. Sign in again.

#### Saved integrations show "Not connected" after I changed `.env`
- You changed `SECRETS_KEY`, or changed `JWT_SECRET` without having set `SECRETS_KEY`. Old credentials can't be decrypted. Paste them again in Integrations.

#### PayPal **Test connection** fails
| Message | Fix |
|---|---|
| `invalid_client` / 401 | Wrong Client ID or Secret, or **Live** keys pasted into **Sandbox** (or the reverse). Copy them again from the matching toggle |
| `NETWORK_ERROR` / could not reach PayPal | No internet, or a firewall or proxy is blocking `api-m.sandbox.paypal.com` |
| Status stays "Saved · not tested" | Click **Test connection** |

#### The PayPal button doesn't appear on the checkout page
- The PayPal Client ID isn't saved or tested.
- An ad blocker is blocking `paypal.com`. Disable it for localhost.
- The order was already paid or cancelled. The page then says so instead of showing the button.

#### Webhook events show `Verify: FAILURE` or don't arrive
- The **Webhook ID** in RecoverAI must be the ID of *this exact* webhook URL in *this* PayPal app (Sandbox vs Live).
- The tunnel URL changed. Update `PUBLIC_API_URL`, restart the API, and update the URL at PayPal.
- Events sent from PayPal's *Webhooks simulator* are mock events and may not verify. Use a real sandbox checkout.

#### Customer messages say "Preview only"
- Expected when no email provider is connected. Connect Resend (step 9). RecoverAI never pretends a message was sent.

#### No verification or reset email arrives
- Without a Resend connection (saved in Integrations or `RESEND_API_KEY` in `.env`), the link is printed in the API window instead (development only).
- With Resend, check that `EMAIL_FROM` uses a verified domain.

#### "AI summary unavailable" / explanations say "rules-based"
- No AI provider is connected, the key is invalid, or the provider timed out.
- See **Integrations → AI provider → AI usage** for the exact error.

#### "Outcome uncertain" on a payment
- A capture timed out or PayPal returned a server error. RecoverAI does **not** assume the payment failed.
- Click **Reconcile now** to ask PayPal for the real status before any retry.

#### I can't change integrations in the demo workspace
- By design: the demo is shared. Create your own account.

#### SQLite "database is locked"
- Two API processes are running on the same `recoverai.db`. Stop one.

#### I want a completely fresh start
Stop the API, then:
```powershell
Remove-Item C:\dev\recoverai\backend\recoverai.db
```
Start the API again.

---

## 17. First-run checklist

- [ ] `python --version` shows 3.11–3.13 and `node --version` shows 20+
- [ ] `backend\.env` exists with your own `JWT_SECRET` and `SECRETS_KEY`
- [ ] http://localhost:8000/api/health returns `"ok": true`
- [ ] http://localhost:5173 shows the RecoverAI landing page
- [ ] The demo workspace opens; Mission Control → Failure Lab → *Funding source declined* shows the agent's reasoning
- [ ] You created your own account and verified the email (link in the API window or in your inbox)
- [ ] Integrations → PayPal Sandbox → **Test connection** shows **Connected**
- [ ] Payments → **New test checkout** → paid with the sandbox buyer → payment **Completed · SANDBOX**
- [ ] (Optional) Webhook registered; **Recent webhook events** shows `Verify: SUCCESS`
- [ ] (Optional) AI provider connected; Failure Intelligence → **AI summary** works
- [ ] (Optional) Resend connected; the test email arrived
- [ ] (Optional) `python -m pytest -q` → 72 passed
