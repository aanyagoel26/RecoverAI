from fastapi import HTTPException
from sqlalchemy.orm import Session


def owned(db: Session, model, obj_id: str, merchant_id: str):
    """Fetch a tenant row or 404. Cross-tenant access looks identical to 'not found' (no existence leak)."""
    obj = db.get(model, obj_id)
    if not obj or getattr(obj, "merchant_id", None) != merchant_id:
        raise HTTPException(404, "Not found")
    return obj
