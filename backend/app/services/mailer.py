"""Transactional emails to merchants (verification, password reset)."""
import logging

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from . import providers

log = logging.getLogger("recoverai.mail")


def send(db: Session, merchant_id: str | None, to: str, subject: str, text: str, *, dev_link: str | None = None) -> str:
    """Returns SENT | FAILED | NOT_CONFIGURED. Never claims delivery."""
    cfg = providers.email_config(db, merchant_id)
    if not cfg:
        if get_settings().app_env != "production" and dev_link:
            # Development convenience only: production never logs tokens.
            log.warning("Email provider not configured. DEV link for %s: %s", to, dev_link)
        return "NOT_CONFIGURED"
    try:
        r = httpx.post("https://api.resend.com/emails", timeout=15, headers={"Authorization": f"Bearer {cfg['api_key']}"},
                       json={"from": cfg["from_email"], "to": [to], "subject": subject, "text": text})
        return "SENT" if r.status_code < 300 else "FAILED"
    except httpx.HTTPError:
        return "FAILED"
