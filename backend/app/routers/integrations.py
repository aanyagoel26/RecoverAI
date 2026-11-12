"""Integrations: encrypted credential vault (PayPal Sandbox/Live, AI, email, SMS), connection tests, AI usage, Twilio callbacks."""
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import AIUsage, CommunicationLog, Merchant
from ..security import CurrentUser, ai_limiter, current_user, require_approver
from ..services import agent, checkout, llm, providers
from ..services.audit import audit
from ..services.communication import twilio_signature_valid
from ..services.paypal_client import PayPalError, PayPalNotConfigured

router = APIRouter(tags=["integrations"])


@router.get("/api/integrations")
def list_integrations(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    ms = agent.merchant_settings(db, cu.merchant_id)
    ai = providers.ai_config(db, cu.merchant_id)
    base = get_settings().public_api_url.rstrip("/")
    db.commit()
    return {"providers": providers.status(db, cu.merchant_id), "paypal_environment": ms.paypal_environment, "ai_provider": ms.ai_provider,
            "active_ai": {"provider": ai["provider"], "model": ai["model"], "source": ai["source"]} if ai else None,
            "webhook_url": f"{base}/api/webhooks/paypal/m/{cu.merchant_id}", "twilio_callback_url": f"{base}/api/webhooks/twilio/{cu.merchant_id}",
            "encryption": "Fernet (AES-128-CBC + HMAC-SHA256)" + ("" if get_settings().secrets_key else " — key derived from JWT_SECRET; set SECRETS_KEY in production")}


class CredIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


def _not_shared_demo(cu: CurrentUser):
    if cu.user.merchant.is_demo and get_settings().demo_integrations_locked:
        raise HTTPException(403, "The demo workspace is shared, so its integrations can't be changed. Create your own account to connect PayPal, AI, email or SMS.")


@router.put("/api/integrations/{provider}")
def save_credentials(provider: str, body: CredIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    _not_shared_demo(cu)
    if provider not in providers.SPEC:
        raise HTTPException(404, "Unknown provider")
    if any(len(v) > 500 for v in body.values.values()):
        raise HTTPException(422, "Value too long")
    row = providers.save(db, cu.merchant_id, provider, body.values)
    audit(db, cu.merchant_id, "user", "integration.saved", f"{cu.user.full_name} saved {providers.SPEC[provider]['label']} credentials",
          actor_id=cu.id, detail={"fields": sorted(k for k, v in body.values.items() if v)})  # never the values
    db.commit()
    return {"provider": provider, "masked": row.masked, "status": row.status}


@router.delete("/api/integrations/{provider}")
def delete_credentials(provider: str, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    _not_shared_demo(cu)
    providers.delete(db, cu.merchant_id, provider)
    audit(db, cu.merchant_id, "user", "integration.removed", f"{cu.user.full_name} removed {providers.SPEC.get(provider, {}).get('label', provider)} credentials", actor_id=cu.id)
    db.commit()
    return {"ok": True}


class SelectIn(BaseModel):
    ai_provider: str = Field(pattern="^(auto|anthropic|openai|gemini|none)$")


@router.put("/api/integrations-ai-provider")
def select_ai(body: SelectIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    _not_shared_demo(cu)
    ms = agent.merchant_settings(db, cu.merchant_id)
    ms.ai_provider = body.ai_provider
    db.commit()
    return {"ai_provider": ms.ai_provider}


class TestIn(BaseModel):
    to: str | None = Field(None, max_length=320)  # email address or E.164 number for a test message


@router.post("/api/integrations/{provider}/test", dependencies=[Depends(ai_limiter)])
def test_provider(provider: str, body: TestIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    if body.to:
        _not_shared_demo(cu)  # no sending test messages to arbitrary addresses from the shared demo
    mid = cu.merchant_id
    ok, msg = False, ""
    try:
        if provider in ("paypal_sandbox", "paypal_live"):
            env = provider.split("_")[1]
            providers.paypal_for(db, mid, env).access_token(force=True)
            if env == providers.paypal_environment(db, mid):
                checkout.touch_connection(db, mid, True)
            ok, msg = True, f"PayPal {env.title()} issued an access token."
        elif provider in providers.AI_PROVIDERS:
            saved = providers.load(db, mid, provider)
            key = (saved or {}).get("api_key") or providers._env_key(provider)
            if not key:
                raise HTTPException(400, "No API key saved for this provider")
            cfg = {"provider": provider, "api_key": key, "model": (saved or {}).get("model") or providers._default_model(provider),
                   "base_url": providers._base_url(provider, (saved or {}).get("base_url"))}
            started = datetime.now(timezone.utc)
            fn = llm._anthropic if provider == "anthropic" else llm._openai
            data, usage = fn(cfg, [{"role": "user", "content": "Reply with the single word: ready"}], "You are a connectivity test.", None, 60)
            ms_ = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            llm._record(db, mid, cfg, "connectivity_test", True, __import__("time").monotonic() - ms_ / 1000, usage)
            ok, msg = True, f"{cfg['model']} answered “{llm.text_of(data).strip()[:40]}” in {ms_} ms."
        elif provider == "resend":
            cfg = providers.email_config(db, mid)
            if not cfg:
                raise HTTPException(400, "No Resend key saved")
            to = body.to or cu.user.email
            r = httpx.post("https://api.resend.com/emails", timeout=15, headers={"Authorization": f"Bearer {cfg['api_key']}"},
                           json={"from": cfg["from_email"], "to": [to], "subject": "RecoverAI test email", "text": "Your RecoverAI email integration works."})
            ok = r.status_code < 300
            msg = f"Resend accepted a test email to {to} (id {r.json().get('id')})." if ok else f"Resend HTTP {r.status_code}: {r.text[:200]}"
        elif provider == "twilio":
            cfg = providers.sms_config(db, mid)
            if not cfg:
                raise HTTPException(400, "No Twilio credentials saved")
            r = httpx.get(f"https://api.twilio.com/2010-04-01/Accounts/{cfg['account_sid']}.json", auth=(cfg["account_sid"], cfg["auth_token"]), timeout=15)
            ok = r.status_code < 300
            msg = f"Twilio account {r.json().get('friendly_name', '')} ({r.json().get('status')}) verified." if ok else f"Twilio HTTP {r.status_code}: {r.text[:200]}"
            if ok and body.to:
                r2 = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{cfg['account_sid']}/Messages.json", timeout=15,
                                auth=(cfg["account_sid"], cfg["auth_token"]), data={"To": body.to, "From": cfg["from_number"], "Body": "RecoverAI test SMS: your integration works."})
                msg += f" Test SMS: HTTP {r2.status_code}."
        else:
            raise HTTPException(404, "Unknown provider")
    except PayPalNotConfigured as e:
        msg = str(e)
    except PayPalError as e:
        msg = str(e) + (" — check Client ID/Secret belong to the same app and environment." if e.http_status == 401 else "")
    except llm.LLMUnavailable as e:
        msg = f"AI provider error: {e}"
    except httpx.HTTPError as e:
        msg = f"Could not reach provider: {type(e).__name__}"
    providers.mark(db, mid, provider, ok, None if ok else msg)
    audit(db, mid, "user", "integration.test", f"Tested {providers.SPEC.get(provider, {}).get('label', provider)}: {'OK' if ok else 'FAILED'}", actor_id=cu.id)
    db.commit()
    return {"ok": ok, "message": msg}


@router.get("/api/integrations/ai-usage")
def ai_usage(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    q = select(AIUsage).where(AIUsage.merchant_id == cu.merchant_id)
    rows = db.scalars(q.order_by(AIUsage.created_at.desc()).limit(50)).all()
    agg = db.execute(select(func.count(), func.sum(AIUsage.input_tokens), func.sum(AIUsage.output_tokens), func.avg(AIUsage.latency_ms))
                     .where(AIUsage.merchant_id == cu.merchant_id)).one()
    fails = db.scalar(select(func.count()).select_from(AIUsage).where(AIUsage.merchant_id == cu.merchant_id, AIUsage.success.is_(False))) or 0
    return {"summary": {"calls": agg[0] or 0, "failed": fails, "input_tokens": agg[1] or 0, "output_tokens": agg[2] or 0,
                        "avg_latency_ms": int(agg[3] or 0)},
            "items": [{"provider": r.provider, "model": r.model, "purpose": r.purpose, "success": r.success, "latency_ms": r.latency_ms,
                       "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "error": r.error, "created_at": r.created_at.isoformat()} for r in rows]}


@router.post("/api/webhooks/twilio/{merchant_id}")
async def twilio_status(merchant_id: str, request: Request, db: Session = Depends(get_db)):
    """Twilio message status callback, verified with X-Twilio-Signature."""
    if not db.get(Merchant, merchant_id[:32]):
        return JSONResponse({"error": "unknown merchant"}, status_code=404)
    cfg = providers.sms_config(db, merchant_id)
    form = dict((await request.form()).items())
    url = f"{get_settings().public_api_url.rstrip('/')}/api/webhooks/twilio/{merchant_id}"
    if not cfg or not twilio_signature_valid(cfg["auth_token"], url, {k: str(v) for k, v in form.items()}, request.headers.get("x-twilio-signature", "")):
        return JSONResponse({"error": "invalid signature"}, status_code=403)
    log = db.scalar(select(CommunicationLog).where(CommunicationLog.provider_message_id == form.get("MessageSid"),
                                                   CommunicationLog.merchant_id == merchant_id))
    if log:
        st = str(form.get("MessageStatus", "")).lower()
        if st == "delivered":
            log.delivery_status, log.delivered_at = "DELIVERED", datetime.now(timezone.utc)
        elif st in ("failed", "undelivered"):
            log.delivery_status, log.error = "FAILED", f"Twilio: {st} {form.get('ErrorCode') or ''}".strip()
        db.commit()
    return PlainTextResponse("ok")
