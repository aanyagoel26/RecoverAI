from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import serializers as S
from ..db import get_db
from ..deps import owned
from ..models import AIAnalysis, CheckoutSession, Customer, Order, Payment, PaymentEvent, RecoveryAction, RecoveryCase
from ..security import CurrentUser, ai_limiter, current_user
from ..services import agent
from ..services.checkout import sync_from_paypal
from ..services.paypal_client import PayPalError, PayPalNotConfigured

router = APIRouter(prefix="/api/payments", tags=["payments"])

FILTERS = {
    "successful": lambda q: q.where(Payment.status == "COMPLETED"),
    "failed": lambda q: q.where(Payment.status.in_(["DECLINED", "FAILED"])),
    "pending": lambda q: q.where(Payment.status == "PENDING"),
    "incomplete": lambda q: q.where(Payment.status.in_(["CREATED", "APPROVED"])),
    "recovered": lambda q: q.where(Payment.is_recovery_attempt.is_(True), Payment.status == "COMPLETED"),
}


@router.get("")
def list_payments(filter: str = "all", q: str | None = Query(None, max_length=80), source: str | None = None,
                  limit: int = Query(50, le=200), offset: int = 0, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    mid = cu.merchant_id
    query = select(Payment).where(Payment.merchant_id == mid)
    if filter in FILTERS:
        query = FILTERS[filter](query)
    elif filter == "abandoned":
        query = query.where(Payment.order_id.in_(select(CheckoutSession.order_id).where(CheckoutSession.merchant_id == mid, CheckoutSession.status == "ABANDONED")))
    elif filter in ("under_recovery", "attention"):
        statuses = ["IN_PROGRESS"] if filter == "under_recovery" else ["OPEN", "AWAITING_APPROVAL"]
        query = query.where(Payment.order_id.in_(select(RecoveryCase.order_id).where(RecoveryCase.merchant_id == mid, RecoveryCase.status.in_(statuses))),
                            Payment.status != "COMPLETED")
    if source:
        query = query.where(Payment.data_source == source)
    if q:
        like = f"%{q.strip()}%"
        query = query.join(Customer, Customer.id == Payment.customer_id).join(Order, Order.id == Payment.order_id).where(
            or_(Payment.reference.ilike(like), Customer.name.ilike(like), Customer.email.ilike(like), Order.order_number.ilike(like),
                Payment.paypal_order_id.ilike(like)))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Payment.created_at.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "items": [S.payment(p) for p in rows]}


@router.get("/{payment_id}")
def get_payment(payment_id: str, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    p = owned(db, Payment, payment_id, cu.merchant_id)
    events = db.scalars(select(PaymentEvent).where(PaymentEvent.payment_id == p.id).order_by(PaymentEvent.created_at)).all()
    analyses = db.scalars(select(AIAnalysis).where(AIAnalysis.payment_id == p.id).order_by(AIAnalysis.created_at.desc()).limit(5)).all()
    case = db.scalar(select(RecoveryCase).where(RecoveryCase.order_id == p.order_id).order_by(RecoveryCase.created_at.desc()).limit(1))
    siblings = db.scalars(select(Payment).where(Payment.order_id == p.order_id).order_by(Payment.created_at)).all()
    completed = [s for s in siblings if s.status == "COMPLETED"]
    actions = db.scalars(select(RecoveryAction).where(RecoveryAction.case_id == case.id).order_by(RecoveryAction.created_at)).all() if case else []
    sess = db.get(CheckoutSession, p.checkout_session_id) if p.checkout_session_id else None
    return {"payment": S.payment(p), "events": [S.event(e) for e in events], "analyses": [S.analysis(a) for a in analyses],
            "case": S.case(case, actions) if case else None, "order_attempts": [S.payment(s) for s in siblings],
            "checkout_session": S.session(sess) if sess else None,
            "duplicate_warning": (f"This order has {len(completed)} completed payments — possible duplicate charge." if len(completed) > 1 else
                                  "This order is already paid by another attempt; no recovery needed." if completed and p.status != "COMPLETED" else None),
            "raw_response": p.raw_response}


@router.post("/{payment_id}/analyse", dependencies=[Depends(ai_limiter)])
def analyse(payment_id: str, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    p = owned(db, Payment, payment_id, cu.merchant_id)
    if p.status == "COMPLETED":
        raise HTTPException(409, "This payment completed successfully — there is nothing to recover.")
    case = agent.ensure_case(db, payment=p)
    if not case:
        raise HTTPException(409, "The order for this payment is already paid by another attempt.")
    a = agent.analyse_case(db, case, payment=p)
    existing = db.scalar(select(RecoveryAction.id).where(RecoveryAction.case_id == case.id).limit(1))
    if not existing:
        agent.plan_actions(db, case, trigger=f"Merchant requested AI analysis of {p.reference}")
    else:
        agent.record_pipeline_run(db, case, f"Merchant re-ran AI analysis of {p.reference}")
    agent.audit(db, cu.merchant_id, "user", "analysis.requested", f"Ran AI analysis on {p.reference}", actor_id=cu.id,
                entity_type="payment", entity_id=p.id)
    db.commit()
    return {"analysis": S.analysis(a), "case_id": case.id}


@router.post("/{payment_id}/sync")
def sync(payment_id: str, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    """Re-read the authoritative status from PayPal (GET /v2/checkout/orders/{id})."""
    p = owned(db, Payment, payment_id, cu.merchant_id)
    if p.data_source == "DEMO_SIMULATED":
        raise HTTPException(400, "Demo payments are simulated and have no PayPal record to sync.")
    try:
        sync_from_paypal(db, p)
    except PayPalNotConfigured as e:
        raise HTTPException(503, str(e))
    except PayPalError as e:
        raise HTTPException(502, str(e))
    return {"payment": S.payment(p)}
