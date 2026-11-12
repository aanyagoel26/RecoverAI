"""Authentication, authorisation and rate limiting.

Auth uses short-lived bearer JWTs sent in the Authorization header (never cookies), so browser CSRF does not apply
to the API. CORS is restricted to the configured frontend origin.
"""
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User

_bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(user: User) -> str:
    s = get_settings()
    payload = {
        "sub": user.id,
        "mid": user.merchant_id,
        "role": user.role,
        "tv": user.token_version or 0,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=s.jwt_ttl_minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


class CurrentUser:
    def __init__(self, user: User):
        self.user = user
        self.id = user.id
        self.merchant_id = user.merchant_id
        self.role = user.role

    @property
    def can_approve(self) -> bool:
        return self.role in ("owner", "admin")


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)) -> CurrentUser:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = db.get(User, payload.get("sub"))
    if not user or user.merchant_id != payload.get("mid"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token subject")
    if payload.get("tv", 0) != (user.token_version or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session was signed out. Please sign in again.")
    return CurrentUser(user)


def require_approver(cu: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not cu.can_approve:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot approve or execute recovery actions")
    return cu


class RateLimiter:
    """Simple in-process sliding-window limiter. For multi-instance deployments put a shared limiter
    (e.g. Redis or the platform's edge rate limiting) in front."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self.hits: dict[str, deque] = defaultdict(deque)

    def __call__(self, request: Request):
        if get_settings().app_env == "test":
            return
        key = f"{request.url.path}:{request.client.host if request.client else 'anon'}"
        q, t = self.hits[key], time.monotonic()
        while q and t - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, slow down")
        q.append(t)


auth_limiter = RateLimiter(limit=10, window_seconds=60)
ai_limiter = RateLimiter(limit=30, window_seconds=60)
public_limiter = RateLimiter(limit=60, window_seconds=60)
webhook_limiter = RateLimiter(limit=300, window_seconds=60)
