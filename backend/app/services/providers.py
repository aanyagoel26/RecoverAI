"""Provider resolution: merchant-saved credentials (encrypted vault) first, then environment variables.

Secrets are encrypted with Fernet before they touch the database and are never returned to the browser — the API
only ever exposes masked values. Sandbox and Live PayPal credentials are stored and selected separately.
"""
import base64
import hashlib
import json
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import IntegrationCredential, MerchantSettings

SANDBOX_URL = "https://api-m.sandbox.paypal.com"
LIVE_URL = "https://api-m.paypal.com"

SPEC = {
    "paypal_sandbox": {"label": "PayPal Sandbox", "fields": {"client_id": True, "client_secret": True, "webhook_id": False}, "secret": ["client_secret"]},
    "paypal_live": {"label": "PayPal Live", "fields": {"client_id": True, "client_secret": True, "webhook_id": False}, "secret": ["client_secret"]},
    "anthropic": {"label": "Anthropic (Claude)", "fields": {"api_key": True, "model": False}, "secret": ["api_key"]},
    
"openai": {
    "label": "OpenAI / OpenAI-compatible",
    "fields": {
        "api_key": True,
        "model": False,
        "base_url": False,
    },
    "secret": ["api_key"],
},

"gemini": {
    "label": "Google Gemini (free tier available)",
    "fields": {
        "api_key": True,
        "model": False,
    },
    "secret": ["api_key"],
},

    "resend": {"label": "Resend (email)", "fields": {"api_key": True, "from_email": True}, "secret": ["api_key"]},
    "twilio": {"label": "Twilio (SMS)", "fields": {"account_sid": True, "auth_token": True, "from_number": True}, "secret": ["auth_token"]},
}


def _fernet() -> Fernet:
    """SECRETS_KEY may be a Fernet key or any long random string (it is then stretched with SHA-256).
    Without it, the key is derived from JWT_SECRET — fine for development, but rotating JWT_SECRET would then make
    saved credentials unreadable, so production should set SECRETS_KEY."""
    s = get_settings()
    if s.secrets_key:
        try:
            return Fernet(s.secrets_key.encode())
        except (ValueError, TypeError):
            return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("recoverai-vault:" + s.secrets_key).encode()).digest()))
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("recoverai-vault:" + s.jwt_secret).encode()).digest()))


def _mask(field: str, value: str, secret: bool) -> str:
    if not value:
        return ""
    if secret:
        return "••••••" + value[-4:] if len(value) > 8 else "••••"
    return value if len(value) <= 12 else f"{value[:6]}…{value[-4:]}"


def _row(db: Session, mid: str, provider: str) -> IntegrationCredential | None:
    return db.scalar(select(IntegrationCredential).where(IntegrationCredential.merchant_id == mid, IntegrationCredential.provider == provider))


def load(db: Session, mid: str | None, provider: str) -> dict | None:
    if not mid:
        return None
    row = _row(db, mid, provider)
    if not row:
        return None
    try:
        return json.loads(_fernet().decrypt(row.encrypted_payload.encode()))
    except InvalidToken:
        return None  # key rotated: treat as not configured rather than crash


def save(db: Session, mid: str, provider: str, values: dict) -> IntegrationCredential:
    if provider not in SPEC:
        raise HTTPException(400, "Unknown provider")
    spec = SPEC[provider]
    current = load(db, mid, provider) or {}
    merged = dict(current)
    for f in spec["fields"]:
        v = values.get(f)
        if isinstance(v, str) and v.strip():
            merged[f] = v.strip()[:500]
    missing = [f for f, req in spec["fields"].items() if req and not merged.get(f)]
    if missing:
        raise HTTPException(422, f"Missing required field(s): {', '.join(missing)}")
    if provider == "twilio" and not merged["from_number"].startswith("+"):
        raise HTTPException(422, "from_number must be in E.164 format, e.g. +15551234567")
    row = _row(db, mid, provider) or IntegrationCredential(merchant_id=mid, provider=provider, encrypted_payload="", masked={})
    row.encrypted_payload = _fernet().encrypt(json.dumps(merged).encode()).decode()
    row.masked = {f: _mask(f, merged.get(f, ""), f in spec["secret"]) for f in spec["fields"]}
    row.status, row.last_error = "CONFIGURED", None
    db.add(row)
    db.flush()
    return row


def delete(db: Session, mid: str, provider: str):
    row = _row(db, mid, provider)
    if row:
        db.delete(row)


def mark(db: Session, mid: str, provider: str, ok: bool, error: str | None = None):
    row = _row(db, mid, provider)
    if row:
        row.status = "CONNECTED" if ok else "ERROR"
        row.last_tested_at = datetime.now(timezone.utc)
        row.last_error = None if ok else (error or "")[:500]


def merchant_settings(db: Session, mid: str) -> MerchantSettings | None:
    return db.scalar(select(MerchantSettings).where(MerchantSettings.merchant_id == mid)) if mid else None


# ---------------------------------------------------------------- PayPal
def paypal_environment(db: Session, mid: str | None) -> str:
    ms = merchant_settings(db, mid) if mid else None
    if ms and ms.paypal_environment in ("sandbox", "live"):
        return ms.paypal_environment
    return "sandbox" if get_settings().paypal_is_sandbox else "live"


def paypal_config(db: Session, mid: str | None, environment: str | None = None) -> dict | None:
    env = environment or paypal_environment(db, mid)
    saved = load(db, mid, f"paypal_{env}")
    if saved:
        return {"client_id": saved["client_id"], "client_secret": saved["client_secret"], "webhook_id": saved.get("webhook_id") or "",
                "base_url": SANDBOX_URL if env == "sandbox" else LIVE_URL, "environment": env, "source": "dashboard"}
    s = get_settings()
    env_matches = (env == "sandbox") == s.paypal_is_sandbox
    if s.paypal_configured and env_matches:
        return {"client_id": s.paypal_client_id, "client_secret": s.paypal_client_secret, "webhook_id": s.paypal_webhook_id,
                "base_url": s.paypal_base_url, "environment": env, "source": "environment"}
    return None


def paypal_for(db: Session, mid: str | None, environment: str | None = None):
    from .paypal_client import PayPalNotConfigured, client_for
    cfg = paypal_config(db, mid, environment)
    if not cfg:
        raise PayPalNotConfigured(f"PayPal {environment or paypal_environment(db, mid)} credentials are not configured")
    return client_for(cfg["client_id"], cfg["client_secret"], cfg["base_url"])


def paypal_data_source(db: Session, mid: str | None) -> str:
    return "PAYPAL_SANDBOX" if paypal_environment(db, mid) == "sandbox" else "PAYPAL_LIVE"


# ---------------------------------------------------------------- AI

AI_PROVIDERS = ("anthropic", "openai", "gemini")


def _auto_order(preferred: str) -> list[str]:
    """'auto' tries every provider; a server-level preference (AI_PROVIDER)
    goes first.
    """
    order = list(AI_PROVIDERS)

    if preferred in AI_PROVIDERS:
        order.remove(preferred)
        order.insert(0, preferred)

    return order


def _env_key(prov: str) -> str:
    s = get_settings()

    return {
        "anthropic": s.anthropic_api_key,
        "openai": s.openai_api_key,
        "gemini": s.gemini_api_key,
    }[prov]


def _default_model(prov: str) -> str:
    s = get_settings()

    return {
        "anthropic": s.anthropic_model,
        "openai": s.openai_model,
        "gemini": s.gemini_model,
    }[prov]


def _base_url(prov: str, saved: str | None) -> str:
    s = get_settings()

    if prov == "anthropic":
        return ""

    if prov == "gemini":
        return s.gemini_base_url.rstrip("/")  # fixed Google endpoint

    return (saved or s.openai_base_url).rstrip("/")


def ai_config(db: Session | None, mid: str | None) -> dict | None:
    s = get_settings()

    choice = "auto"

    if db is not None and mid:
        ms = merchant_settings(db, mid)
        choice = ms.ai_provider if ms else "auto"

    if choice == "none":
        return None

    order = (
        [choice]
        if choice in AI_PROVIDERS
        else _auto_order(s.ai_provider)
    )

    for prov in order:
        saved = load(db, mid, prov) if db is not None else None

        if saved:
            return {
                "provider": prov,
                "api_key": saved["api_key"],
                "source": "dashboard",
                "model": saved.get("model") or _default_model(prov),
                "base_url": _base_url(prov, saved.get("base_url")),
            }

        env_key = _env_key(prov)

        if env_key:
            return {
                "provider": prov,
                "api_key": env_key,
                "model": _default_model(prov),
                "base_url": _base_url(prov, None),
                "source": "environment",
            }

    return None


# ----------------------------------------------------------------
# Email / SMS

def email_config(db: Session | None, mid: str | None) -> dict | None:
    saved = load(db, mid, "resend") if db is not None else None
    if saved:
        return {"api_key": saved["api_key"], "from_email": saved["from_email"], "source": "dashboard"}
    s = get_settings()
    return {"api_key": s.resend_api_key, "from_email": s.email_from, "source": "environment"} if s.resend_api_key else None


def sms_config(db: Session | None, mid: str | None) -> dict | None:
    saved = load(db, mid, "twilio") if db is not None else None
    if saved:
        return {**saved, "source": "dashboard"}
    s = get_settings()
    if s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number:
        return {"account_sid": s.twilio_account_sid, "auth_token": s.twilio_auth_token, "from_number": s.twilio_from_number, "source": "environment"}
    return None


def status(db: Session, mid: str) -> dict:
    out = {}
    for prov, spec in SPEC.items():
        row = _row(db, mid, prov)
        out[prov] = {"label": spec["label"], "fields": list(spec["fields"]), "required": [f for f, r in spec["fields"].items() if r],
                     "secret_fields": spec["secret"], "saved": bool(row), "masked": row.masked if row else None,
                     "status": row.status if row else "NOT_CONFIGURED", "last_tested_at": row.last_tested_at.isoformat() if row and row.last_tested_at else None,
                     "last_error": row.last_error if row else None}
    s = get_settings()
    out["paypal_sandbox"]["env_fallback"] = bool(s.paypal_configured and s.paypal_is_sandbox)
    out["paypal_live"]["env_fallback"] = bool(s.paypal_configured and not s.paypal_is_sandbox)
    out["anthropic"]["env_fallback"] = bool(s.anthropic_api_key)
    out["openai"]["env_fallback"] = bool(s.openai_api_key)
    out["gemini"]["env_fallback"] = bool(s.gemini_api_key)
    out["resend"]["env_fallback"] = bool(s.resend_api_key)
    out["twilio"]["env_fallback"] = bool(s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number)
    return out
