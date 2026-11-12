from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import serializers as S
from ..db import get_db
from ..models import CheckoutSession, Customer, Merchant, Payment, Product, WebhookEvent
from ..security import CurrentUser, current_user, require_approver
from ..seed import reset_demo
from ..services import agent, webhooks
from ..services.audit import audit

router = APIRouter(tags=["merchant"])


def _settings(s) -> dict:
    return {"recovery_mode": s.recovery_mode, "reminder_intervals_minutes": s.reminder_intervals_minutes,
            "max_reminders_per_case": s.max_reminders_per_case, "min_minutes_between_messages": s.min_minutes_between_messages,
            "abandonment_threshold_minutes": s.abandonment_threshold_minutes, "pending_alert_after_minutes": s.pending_alert_after_minutes,
            "default_tone": s.default_tone, "brand_name": s.brand_name, "support_email": s.support_email,
            "max_retry_attempts": s.max_retry_attempts, "retry_cooldown_minutes": s.retry_cooldown_minutes,
            "max_auto_amount_minor": s.max_auto_amount_minor, "permitted_auto_actions": s.permitted_auto_actions,
            "escalate_after_hours": s.escalate_after_hours, "quiet_hours_start": s.quiet_hours_start, "quiet_hours_end": s.quiet_hours_end,
            "channels": s.channels, "notify_on": s.notify_on, "paypal_environment": s.paypal_environment, "ai_provider": s.ai_provider}


class SettingsIn(BaseModel):
    recovery_mode: str | None = Field(None, pattern="^(AUTOMATIC|APPROVAL|MANUAL)$")
    reminder_intervals_minutes: list[int] | None = None
    max_reminders_per_case: int | None = Field(None, ge=0, le=5)
    min_minutes_between_messages: int | None = Field(None, ge=5, le=10080)
    abandonment_threshold_minutes: int | None = Field(None, ge=5, le=10080)
    pending_alert_after_minutes: int | None = Field(None, ge=30, le=43200)
    default_tone: str | None = Field(None, pattern="^(professional|friendly|urgent)$")
    brand_name: str | None = Field(None, max_length=200)
    support_email: str | None = Field(None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    max_retry_attempts: int | None = Field(None, ge=0, le=5)
    retry_cooldown_minutes: int | None = Field(None, ge=1, le=1440)
    max_auto_amount_minor: int | None = Field(None, ge=0, le=100_000_000)
    permitted_auto_actions: list[str] | None = None
    escalate_after_hours: int | None = Field(None, ge=1, le=720)
    quiet_hours_start: int | None = Field(None, ge=0, le=23)
    quiet_hours_end: int | None = Field(None, ge=0, le=23)
    channels: list[str] | None = None
    notify_on: list[str] | None = None

    @field_validator("permitted_auto_actions")
    @classmethod
    def _acts(cls, v):
        from ..services.policy import ACTIONS
        if v is not None and any(a not in ACTIONS for a in v):
            raise ValueError("Unknown action in permitted_auto_actions")
        return v

    @field_validator("channels")
    @classmethod
    def _ch(cls, v):
        if v is not None and (not v or any(c not in ("email", "sms") for c in v)):
            raise ValueError("channels must be a non-empty subset of email, sms")
        return v

    @field_validator("notify_on")
    @classmethod
    def _no(cls, v):
        allowed = {"recovered", "approval", "blocked", "escalation", "integration", "duplicate"}
        if v is not None and any(c not in allowed for c in v):
            raise ValueError(f"notify_on must be a subset of {sorted(allowed)}")
        return v

    @field_validator("reminder_intervals_minutes")
    @classmethod
    def _iv(cls, v):
        if v is None:
            return v
        if not 1 <= len(v) <= 5 or any(not 5 <= x <= 20160 for x in v) or v != sorted(v):
            raise ValueError("1–5 ascending intervals between 5 minutes and 14 days")
        return v


@router.get("/api/merchant/settings")
def get_settings_(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return _settings(agent.merchant_settings(db, cu.merchant_id))


@router.put("/api/merchant/settings")
def put_settings(body: SettingsIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    s = agent.merchant_settings(db, cu.merchant_id)
    changes = body.model_dump(exclude_unset=True)
    if "support_email" in changes and changes["support_email"] is None:
        pass
    changes = {k: v for k, v in changes.items() if v is not None or k in ("quiet_hours_start", "quiet_hours_end", "support_email", "brand_name")}
    for k, v in changes.items():
        setattr(s, k, v)
    audit(db, cu.merchant_id, "user", "settings.updated", f"{cu.user.full_name} updated settings: {', '.join(changes)}", actor_id=cu.id, detail=changes)
    db.commit()
    return _settings(s)


class ProfileIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    website: str | None = Field(None, max_length=300)
    industry: str | None = Field(None, max_length=100)
    default_currency: str | None = Field(None, pattern="^[A-Z]{3}$")
    business_type: str | None = Field(None, max_length=60)
    timezone: str | None = Field(None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        if v is None:
            return v
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError("Unknown timezone (use an IANA name like Asia/Kolkata)")
        return v
    full_name: str | None = Field(None, min_length=1, max_length=200)
    onboarding_complete: bool | None = None


@router.put("/api/merchant/profile")
def put_profile(body: ProfileIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    m = db.get(Merchant, cu.merchant_id)
    for k in ("name", "website", "industry", "default_currency", "onboarding_complete", "business_type", "timezone"):
        v = getattr(body, k)
        if v is not None:
            setattr(m, k, v)
    if body.full_name:
        cu.user.full_name = body.full_name
    audit(db, m.id, "user", "profile.updated", f"{cu.user.full_name} updated the merchant profile", actor_id=cu.id)
    db.commit()
    return {"ok": True}


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price_minor: int = Field(gt=0, le=100_000_000)
    sku: str | None = Field(None, max_length=64)


@router.get("/api/merchant/products")
def products(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Product).where(Product.merchant_id == cu.merchant_id).order_by(Product.name)).all()
    return {"items": [{"id": p.id, "name": p.name, "sku": p.sku, "price_minor": p.price_minor, "currency": p.currency} for p in rows]}


@router.post("/api/merchant/products")
def add_product(body: ProductIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    p = Product(merchant_id=cu.merchant_id, name=body.name, price_minor=body.price_minor, sku=body.sku)
    db.add(p)
    db.commit()
    return {"id": p.id}


@router.get("/api/customers")
def customers(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    mid = cu.merchant_id
    rows = db.scalars(select(Customer).where(Customer.merchant_id == mid).order_by(Customer.name)).all()
    out = []
    for c in rows:
        stats = dict(db.execute(select(Payment.status, func.count()).where(Payment.customer_id == c.id).group_by(Payment.status)).all())
        abandoned = db.scalar(select(func.count()).select_from(CheckoutSession).where(CheckoutSession.customer_id == c.id, CheckoutSession.status == "ABANDONED"))
        out.append({"id": c.id, "name": c.name, "email": c.email, "phone": c.phone, "country": c.country, "consent": c.recovery_contact_consent,
                    "is_demo": c.is_demo, "completed": stats.get("COMPLETED", 0), "failed": stats.get("DECLINED", 0) + stats.get("FAILED", 0),
                    "pending": stats.get("PENDING", 0), "abandoned": abandoned})
    return {"items": out}


@router.get("/api/customers/{customer_id}")
def customer_detail(customer_id: str, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    from .. import serializers as S
    from ..models import CommunicationLog, RecoveryCase
    c = db.get(Customer, customer_id)
    if not c or c.merchant_id != cu.merchant_id:
        raise HTTPException(404, "Not found")
    pays = db.scalars(select(Payment).where(Payment.customer_id == c.id).order_by(Payment.created_at.desc()).limit(50)).all()
    cases = db.scalars(select(RecoveryCase).where(RecoveryCase.customer_id == c.id).order_by(RecoveryCase.created_at.desc()).limit(20)).all()
    comms = db.scalars(select(CommunicationLog).where(CommunicationLog.customer_id == c.id).order_by(CommunicationLog.created_at.desc()).limit(20)).all()
    completed = [p for p in pays if p.status in ("COMPLETED", "PARTIALLY_REFUNDED")]
    return {"customer": {"id": c.id, "name": c.name, "email": c.email, "phone": c.phone, "country": c.country, "consent": c.recovery_contact_consent, "is_demo": c.is_demo},
            "stats": {"attempts": len(pays), "completed": len(completed), "failed": sum(1 for p in pays if p.status in ("DECLINED", "FAILED")),
                      "lifetime_paid_minor": sum(p.amount_minor - (p.refunded_minor or 0) for p in completed),
                      "recovered_minor": sum(x.recovered_amount_minor for x in cases if x.status == "RECOVERED")},
            "payments": [S.payment(p) for p in pays], "cases": [S.case(x) for x in cases], "communications": [S.comm(m, c) for m in comms]}


class CustomerIn(BaseModel):
    phone: str | None = Field(None, max_length=32, pattern=r"^\+[1-9]\d{6,14}$")


@router.put("/api/customers/{customer_id}")
def update_customer(customer_id: str, body: CustomerIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    c = db.get(Customer, customer_id)
    if not c or c.merchant_id != cu.merchant_id:
        raise HTTPException(404, "Not found")
    c.phone = body.phone
    audit(db, cu.merchant_id, "user", "customer.updated", f"Updated contact details for {c.name}", actor_id=cu.id)
    db.commit()
    return {"phone": c.phone}


@router.post("/api/customers/{customer_id}/consent")
def set_consent(customer_id: str, body: dict, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    c = db.get(Customer, customer_id)
    if not c or c.merchant_id != cu.merchant_id:
        raise HTTPException(404, "Not found")
    c.recovery_contact_consent = bool(body.get("consent"))
    audit(db, cu.merchant_id, "user", "customer.consent", f"Recovery-message consent for {c.name} set to {c.recovery_contact_consent}", actor_id=cu.id)
    db.commit()
    return {"consent": c.recovery_contact_consent}


# ---------------------------------------------------------------- demo mode
def _demo_merchant(cu: CurrentUser, db: Session) -> Merchant:
    m = db.get(Merchant, cu.merchant_id)
    if not m.is_demo:
        raise HTTPException(403, "Demo tools are only available in the demo workspace")
    return m


@router.post("/api/demo/reset")
def demo_reset(cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    reset_demo(db, _demo_merchant(cu, db))
    return {"ok": True}


class SimWebhookIn(BaseModel):
    payment_id: str
    event_type: str = Field(pattern="^(PAYMENT\\.CAPTURE\\.(COMPLETED|PENDING|DENIED|REFUNDED)|CHECKOUT\\.ORDER\\.APPROVED)$")


@router.post("/api/demo/simulate-webhook")
def demo_webhook(body: SimWebhookIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    p = db.get(Payment, body.payment_id)
    if not p or p.merchant_id != cu.merchant_id:
        raise HTTPException(404, "Not found")
    try:
        return webhooks.simulate(db, p, body.event_type)
    except webhooks.WebhookRejected as e:
        raise HTTPException(400, str(e))


@router.post("/api/demo/replay-webhook")
def demo_replay(body: dict, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    w = db.scalar(select(WebhookEvent).where(WebhookEvent.paypal_event_id == str(body.get("event_id", "")), WebhookEvent.merchant_id == cu.merchant_id))
    if not w:
        raise HTTPException(404, "Not found")
    return webhooks.replay_duplicate(db, w.paypal_event_id)
