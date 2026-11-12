# RecoverAI API

Base URL: `http://localhost:8000`. Interactive docs: `/docs` (OpenAPI).
Auth: `Authorization: Bearer <jwt>` from `/api/auth/login|signup|demo`. Every authenticated query is scoped to the caller's merchant, and cross-tenant IDs return 404. Approve/execute/settings endpoints require role `owner` or `admin`; `analyst` is read-only.

## Auth
| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/signup` | `{email, password(≥10), full_name, business_name}` → token |
| POST | `/api/auth/login` | rate limited |
| POST | `/api/auth/demo` | signs into the labelled demo workspace |
| GET | `/api/auth/me` | |
| POST | `/api/auth/verify-email` | `{token}` from the verification email |
| POST | `/api/auth/resend-verification` | signed in |
| POST | `/api/auth/forgot-password` | `{email}`. Always answers the same way (no account enumeration) |
| POST | `/api/auth/reset-password` | `{token, password}`. Single-use, 1 h; signs out every session |
| POST | `/api/auth/change-password` | `{current_password, new_password}` |
| POST | `/api/auth/logout-all` | invalidates every token for this user |

## PayPal
| Method | Path | Notes |
|---|---|---|
| GET | `/api/paypal/status` | credentials, connection, webhook config, last webhook, health |
| POST | `/api/paypal/connect` | **Test connection**: real OAuth token request |
| GET | `/api/paypal/client-config` | public client id/currency for the JS SDK (never the secret) |
| POST | `/api/paypal/test-checkout` | creates order + checkout session, returns `/pay/<token>` link |
| POST | `/api/paypal/create-order` | `{order_id}` → PayPal order (merchant-side) |
| POST | `/api/paypal/capture-order` | `{paypal_order_id}` (merchant-side) |
| POST | `/api/paypal/environment` | `{environment: sandbox|live, confirm_live}`. Live requires `confirm_live: true` |
| POST | `/api/webhooks/paypal/m/{merchant_id}` | per-merchant PayPal webhook (verified with that merchant's webhook ID) |
| POST | `/api/webhooks/paypal` | server-wide PayPal webhook. Verified, de-duplicated, idempotent |
| GET | `/api/webhooks/events` | recent events for this merchant |

## Customer checkout (public, token-scoped, rate limited)
| Method | Path | Notes |
|---|---|---|
| GET | `/api/public/checkout/{token}` | order summary, paid/pending flags, JS SDK config |
| POST | `/api/public/checkout/{token}/create-order` | PayPal order (reuses a recent one) |
| POST | `/api/public/checkout/{token}/capture` | `{paypal_order_id}`; result comes from PayPal's capture response |
| POST | `/api/public/checkout/{token}/simulate` | **demo orders only**: `{outcome: complete|pending|decline}` |
| POST | `/api/public/checkout/{token}/activity` | `{c?}` heartbeat for abandonment detection; `c` = message ID for click tracking |

## Payments
| Method | Path | Notes |
|---|---|---|
| GET | `/api/payments?filter=&q=&source=&limit=&offset=` | filters: all, successful, failed, pending, abandoned, incomplete, under_recovery, recovered, attention |
| GET | `/api/payments/{id}` | payment, lifecycle events, analyses, case, sibling attempts, duplicate warning |
| POST | `/api/payments/{id}/analyse` | AI failure analysis (opens/plans a case if needed) |
| POST | `/api/payments/{id}/sync` | authoritative refresh via `GET /v2/checkout/orders/{id}` |
| POST | `/api/payments/{id}/reconcile` | reconcile an uncertain outcome with PayPal; returns `{result: {paypal_status, capture_status, changed, note}, payment}` |
| POST | `/api/orders/{id}/cancel` | `{reason?}`. Refused (409) if paid or pending; closes cases and stops messages |
| GET | `/api/firewall/blocked` | actions the Trust Firewall blocked, with guardrail results |

## Recovery
| Method | Path | Notes |
|---|---|---|
| GET | `/api/recovery/cases?status=active|<STATUS>|all&sort=priority|revenue|probability|oldest|newest&view=urgent|approval` | ranked queue with reasons |
| GET | `/api/recovery/cases/{id}` | case, actions, latest analysis, communications |
| POST | `/api/recovery/{id}/approve` | `{action_id?, tone?, subject?, body?}`; validated by the policy engine, then executed |
| POST | `/api/recovery/{id}/execute` | alias of approve for non-message actions |
| POST | `/api/recovery/actions/{action_id}/reject` | |
| POST | `/api/recovery/{id}/close` | `{outcome: CLOSED_UNRECOVERED|CLOSED_ALREADY_PAID}` |
| POST | `/api/recovery/{id}/reanalyse` | |
| POST | `/api/recovery/{id}/preview-message` | `{tone}`, draft only |

## Agent, AI, analytics
| Method | Path | Notes |
|---|---|---|
| POST | `/api/agent/run` | one monitoring pass (also runs every `AGENT_LOOP_SECONDS`) |
| GET | `/api/agent/activity` · `/api/agent/approvals` · `/api/agent/actions?status=` | |
| POST | `/api/ai/chat` | Copilot `{message, history}` → `{answer, tool_calls, generated_by, confirmation}` |
| GET | `/api/analytics/overview?days=|start=&end=&source=` | KPIs + definitions |
| GET | `/api/analytics/revenue` · `/api/analytics/breakdowns` | time series, categories, strategies, customers |
| GET | `/api/analytics/export.csv?kind=payments|cases|daily` | |
| POST | `/api/simulator/run` | `{scenarios?, assumptions?, at_risk_override_minor?}` |
| GET | `/api/communications` · POST `/api/communications/test` | test sends go to the merchant's own email |

## Mission Control
| Method | Path | Notes |
|---|---|---|
| GET | `/api/agent/runs?case_id=&limit=` | Trust Firewall traces: steps, guardrails, rail, outcome |
| GET | `/api/lab/scenarios` | Failure Lab scenarios + whether real PayPal Sandbox is available |
| POST | `/api/lab/run` | `{scenario, customer_name?, customer_email?, country?}`: real sandbox order + negative-testing capture (or labelled demo) |
| POST | `/api/recovery/{id}/invoice/sync` | `GET /v2/invoicing/invoices/{id}`, records payment if PAID |

## Integrations (credential vault)
| Method | Path | Notes |
|---|---|---|
| GET | `/api/integrations` | per-provider status with **masked** values only, webhook and Twilio callback URLs, active AI provider |
| PUT | `/api/integrations/{provider}` | `{values: {...}}`. Provider is one of `paypal_sandbox`, `paypal_live`, `anthropic`, `openai`, `resend`, `twilio`. Empty fields keep the saved value. Locked on the shared demo |
| DELETE | `/api/integrations/{provider}` | remove saved credentials |
| POST | `/api/integrations/{provider}/test` | `{to?}`: real connection test (and optional test email/SMS) |
| PUT | `/api/integrations-ai-provider` | `{ai_provider: auto|anthropic|openai|none}` |
| GET | `/api/integrations/ai-usage` | AI call log (no prompts, outputs or keys) + summary |
| POST | `/api/webhooks/twilio/{merchant_id}` | Twilio status callback (signature verified) |

## Intelligence
| Method | Path | Notes |
|---|---|---|
| GET | `/api/intelligence?days=&start=&end=&category=&group=&source=&method=` | totals, categories, groups, trend, patterns, repeat customers/orders, outcomes, insights |
| POST | `/api/intelligence/ai-summary?days=` | AI summary, or `{available: false, reason}` |
| GET | `/api/insights` | top 4 computed insights for the dashboard |

## Merchant
`GET/PUT /api/merchant/settings` (incl. retry limit/cooldown, automation amount limit, allowed auto actions, escalation, quiet hours, channels, notify_on) · `PUT /api/merchant/profile` (incl. business_type, timezone) · `GET/POST /api/merchant/products` · `GET /api/customers` · `GET /api/customers/{id}` (detail) · `PUT /api/customers/{id}` (`{phone}` E.164) · `POST /api/customers/{id}/consent` · `GET /api/notifications` · `POST /api/notifications/read` · `GET /api/audit` · demo-only: `POST /api/demo/reset`, `/api/demo/simulate-webhook`, `/api/demo/replay-webhook`

## Copilot tools
`get_pending_approvals`, `get_blocked_actions`, `get_payment_analytics`, `get_failed_payments`, `analyse_transaction`, `get_recovery_cases`, `calculate_recovery_probability`, `generate_recovery_message`, `create_recovery_case`, `send_approved_recovery_message` (prepares a one-click approval and never sends), `get_revenue_forecast`, `get_abandoned_checkouts`.

Errors are JSON `{detail}`. Validation errors also include `errors[]`. Money is integer minor units (`*_minor`).
