from sqlalchemy.orm import Session

from ..models import AuditLog, MerchantSettings, Notification


def audit(db: Session, merchant_id: str | None, actor_type: str, action: str, summary: str, *, actor_id: str | None = None,
          entity_type: str | None = None, entity_id: str | None = None, detail: dict | None = None) -> AuditLog:
    row = AuditLog(merchant_id=merchant_id, actor_type=actor_type, actor_id=actor_id, action=action,
                   entity_type=entity_type, entity_id=entity_id, summary=summary, detail=detail)
    db.add(row)
    return row


def notify(db: Session, merchant_id: str, kind: str, title: str, body: str, link: str | None = None,
           category: str | None = None) -> Notification | None:
    """category lets merchants mute kinds of alerts (Settings → Notifications)."""
    if category:
        ms = db.query(MerchantSettings).filter(MerchantSettings.merchant_id == merchant_id).one_or_none()
        if ms and ms.notify_on is not None and category not in ms.notify_on:
            return None
    n = Notification(merchant_id=merchant_id, kind=kind, title=title, body=body, link=link)
    db.add(n)
    return n


def money(minor: int, currency: str = "USD") -> str:
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}.get(currency, currency + " ")
    return f"{sym}{minor / 100:,.2f}"
