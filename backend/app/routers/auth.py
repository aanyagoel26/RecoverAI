from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
import hashlib
import secrets
from datetime import timedelta

from ..config import get_settings
from ..models import AuthToken, Merchant, MerchantSettings, PayPalConnection, User
from ..services import mailer
from ..services.agent import aware
from ..security import CurrentUser, auth_limiter, create_token, current_user, hash_password, verify_password
from ..seed import DEMO_EMAIL, DEMO_PASSWORD, ensure_demo
from ..services.agent import utcnow
from ..services.audit import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    business_name: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


def _me(u: User) -> dict:
    m = u.merchant
    return {"user": {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "email_verified": u.email_verified},
            "merchant": {"id": m.id, "name": m.name, "is_demo": m.is_demo, "onboarding_complete": m.onboarding_complete,
                         "website": m.website, "industry": m.industry, "default_currency": m.default_currency,
                         "business_type": m.business_type, "timezone": m.timezone}}


def _issue_token(db: Session, user: User, kind: str, hours: int) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(AuthToken(user_id=user.id, kind=kind, token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                     expires_at=utcnow() + timedelta(hours=hours)))
    return raw


def _consume(db: Session, raw: str, kind: str) -> User:
    t = db.scalar(select(AuthToken).where(AuthToken.token_hash == hashlib.sha256(raw.encode()).hexdigest(), AuthToken.kind == kind))
    if not t or t.used_at or aware(t.expires_at) < utcnow():
        raise HTTPException(400, "This link is invalid or has expired. Request a new one.")
    t.used_at = utcnow()
    return db.get(User, t.user_id)


def _send_verification(db: Session, u: User) -> str:
    link = f"{get_settings().frontend_url.rstrip('/')}/verify-email?token={_issue_token(db, u, 'verify', 48)}"
    return mailer.send(db, u.merchant_id, u.email, "Verify your RecoverAI email",
                       f"Hi {u.full_name.split()[0]},\n\nConfirm your email for RecoverAI: {link}\n\nThis link expires in 48 hours.", dev_link=link)


@router.post("/signup", dependencies=[Depends(auth_limiter)])
def signup(body: SignupIn, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "An account with this email already exists")
    m = Merchant(name=body.business_name.strip())
    db.add(m)
    db.flush()
    u = User(merchant_id=m.id, email=body.email.lower(), full_name=body.full_name.strip(), password_hash=hash_password(body.password), role="owner")
    db.add_all([u, MerchantSettings(merchant_id=m.id, brand_name=m.name), PayPalConnection(merchant_id=m.id)])
    audit(db, m.id, "user", "auth.signup", f"{u.full_name} created the {m.name} workspace", actor_id=u.id)
    db.flush()
    delivery = _send_verification(db, u)
    db.commit()
    return {"token": create_token(u), **_me(u), "verification_email": delivery}


@router.post("/login", dependencies=[Depends(auth_limiter)])
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    u.last_login_at = utcnow()
    audit(db, u.merchant_id, "user", "auth.login", f"{u.full_name} signed in", actor_id=u.id)
    db.commit()
    return {"token": create_token(u), **_me(u)}


@router.post("/demo", dependencies=[Depends(auth_limiter)])
def demo_login(db: Session = Depends(get_db)):
    ensure_demo(db)
    return login(LoginIn(email=DEMO_EMAIL, password=DEMO_PASSWORD), db)


@router.get("/me")
def me(cu: CurrentUser = Depends(current_user)):
    return _me(cu.user)


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


@router.post("/verify-email", dependencies=[Depends(auth_limiter)])
def verify_email(body: TokenIn, db: Session = Depends(get_db)):
    u = _consume(db, body.token, "verify")
    u.email_verified = True
    audit(db, u.merchant_id, "user", "auth.email_verified", f"{u.email} verified their email", actor_id=u.id)
    db.commit()
    return {"ok": True}


@router.post("/resend-verification", dependencies=[Depends(auth_limiter)])
def resend_verification(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    if cu.user.email_verified:
        return {"status": "ALREADY_VERIFIED"}
    st = _send_verification(db, cu.user)
    db.commit()
    return {"status": st}


class ForgotIn(BaseModel):
    email: EmailStr


@router.post("/forgot-password", dependencies=[Depends(auth_limiter)])
def forgot_password(body: ForgotIn, db: Session = Depends(get_db)):
    """Always answers the same way, so it can't be used to discover which emails have accounts."""
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    if u:
        link = f"{get_settings().frontend_url.rstrip('/')}/reset-password?token={_issue_token(db, u, 'reset', 1)}"
        mailer.send(db, u.merchant_id, u.email, "Reset your RecoverAI password",
                    f"Someone asked to reset your RecoverAI password. If it was you, open: {link}\n\nThe link expires in 1 hour. "
                    "If it wasn't you, ignore this email.", dev_link=link)
        audit(db, u.merchant_id, "user", "auth.reset_requested", "Password reset requested", actor_id=u.id)
        db.commit()
    from ..services import providers
    return {"ok": True, "email_delivery": "configured" if providers.email_config(db, None) else "not_configured"}


class ResetIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=128)


@router.post("/reset-password", dependencies=[Depends(auth_limiter)])
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    u = _consume(db, body.token, "reset")
    u.password_hash = hash_password(body.password)
    u.token_version = (u.token_version or 0) + 1  # sign out every existing session
    u.email_verified = True  # they proved control of the inbox
    audit(db, u.merchant_id, "user", "auth.password_reset", "Password reset; all sessions signed out", actor_id=u.id)
    db.commit()
    return {"ok": True}


class ChangeIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


@router.post("/change-password", dependencies=[Depends(auth_limiter)])
def change_password(body: ChangeIn, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    if cu.user.merchant.is_demo:
        raise HTTPException(403, "The shared demo account's password can't be changed")
    if not verify_password(body.current_password, cu.user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    cu.user.password_hash = hash_password(body.new_password)
    cu.user.token_version = (cu.user.token_version or 0) + 1
    audit(db, cu.merchant_id, "user", "auth.password_changed", "Password changed; other sessions signed out", actor_id=cu.id)
    db.commit()
    return {"token": create_token(cu.user)}


@router.post("/logout-all")
def logout_all(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    if cu.user.merchant.is_demo:
        raise HTTPException(403, "Not available on the shared demo account")
    cu.user.token_version = (cu.user.token_version or 0) + 1
    audit(db, cu.merchant_id, "user", "auth.logout_all", "Signed out of all sessions", actor_id=cu.id)
    db.commit()
    return {"ok": True}
