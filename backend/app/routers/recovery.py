from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import serializers as S
from ..db import get_db
from ..deps import owned
from ..models import AgentRun, AIAnalysis, CommunicationLog, Customer, PayPalInvoice, RecoveryAction, RecoveryCase
from ..security import CurrentUser, ai_limiter, current_user, require_approver
from ..services import agent, communication
from ..services.audit import audit

router = APIRouter(prefix="/api/recovery", tags=["recovery"])

SORTS = {
    "priority": lambda c: -(c.priority_score or 0),
    "revenue": lambda c: -c.amount_minor,
    "probability": lambda c: -(c.recovery_probability or 0),
    "oldest": lambda c: c.created_at,
    "newest": lambda c: -c.created_at.timestamp(),
}


@router.get("/cases")
def list_cases(status: str = "active", sort: str = "priority", view: str | None = None, cu: CurrentUser = Depends(current_user),
               db: Session = Depends(get_db)):
    q = select(RecoveryCase).where(RecoveryCase.merchant_id == cu.merchant_id)
    if status == "active":
        q = q.where(RecoveryCase.status.in_(agent.ACTIVE_CASE))
    elif status != "all":
        q = q.where(RecoveryCase.status == status)
    cases = db.scalars(q).all()
    pending = {a.case_id for a in db.scalars(select(RecoveryAction).where(RecoveryAction.merchant_id == cu.merchant_id, RecoveryAction.status == "PROPOSED"))}
    if view == "urgent":
        cases = [c for c in cases if (c.priority_score or 0) >= 50 or c.failure_category == "AUTHENTICATION_REQUIRED"]
    elif view == "approval":
        cases = [c for c in cases if c.id in pending]
    cases.sort(key=SORTS.get(sort, SORTS["priority"]))
    out = []
    for i, c in enumerate(cases):
        d = S.case(c)
        d["rank"] = i + 1
        d["awaiting_approval"] = c.id in pending
        out.append(d)
    return {"items": out}


@router.get("/cases/{case_id}")
def get_case(case_id: str, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    c = owned(db, RecoveryCase, case_id, cu.merchant_id)
    actions = db.scalars(select(RecoveryAction).where(RecoveryAction.case_id == c.id).order_by(RecoveryAction.created_at)).all()
    analysis = db.scalar(select(AIAnalysis).where(AIAnalysis.case_id == c.id).order_by(AIAnalysis.created_at.desc()).limit(1))
    comms = db.scalars(select(CommunicationLog).where(CommunicationLog.case_id == c.id).order_by(CommunicationLog.created_at.desc())).all()
    cust = db.get(Customer, c.customer_id)
    runs = db.scalars(select(AgentRun).where(AgentRun.case_id == c.id).order_by(AgentRun.created_at.desc()).limit(10)).all()
    invs = db.scalars(select(PayPalInvoice).where(PayPalInvoice.case_id == c.id).order_by(PayPalInvoice.created_at.desc())).all()
    return {"case": S.case(c, actions), "analysis": S.analysis(analysis) if analysis else None, "communications": [S.comm(m, cust) for m in comms],
            "runs": [S.run(r) for r in runs], "invoices": [S.invoice(i) for i in invs]}


class ApproveIn(BaseModel):
    action_id: str | None = None
    tone: str | None = Field(None, pattern="^(professional|friendly|urgent)$")
    subject: str | None = Field(None, max_length=300)
    body: str | None = Field(None, max_length=5000)


def _pick_action(db, case, action_id):
    if action_id:
        a = owned(db, RecoveryAction, action_id, case.merchant_id)
        if a.case_id != case.id:
            raise HTTPException(404, "Action not on this case")
        return a
    a = db.scalar(select(RecoveryAction).where(RecoveryAction.case_id == case.id, RecoveryAction.status.in_(["PROPOSED"])).order_by(RecoveryAction.created_at))
    if not a:
        raise HTTPException(409, "No action is waiting for approval on this case")
    return a


@router.post("/{case_id}/approve", dependencies=[Depends(ai_limiter)])
def approve(case_id: str, body: ApproveIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    """Merchant approves a proposed action; the backend validates it with the policy engine and executes it."""
    c = owned(db, RecoveryCase, case_id, cu.merchant_id)
    a = _pick_action(db, c, body.action_id)
    if a.status not in ("PROPOSED", "SCHEDULED", "APPROVED"):
        raise HTTPException(409, f"Action is {a.status}")
    a.status = "APPROVED"
    audit(db, cu.merchant_id, "user", "action.approved", f"{cu.user.full_name} approved {a.action_type.replace('_', ' ').lower()}",
          actor_id=cu.id, entity_type="recovery_action", entity_id=a.id)
    agent.execute_action(db, a, actor_type="user", actor_id=cu.id, tone=body.tone, subject=body.subject, body=body.body)
    db.commit()
    return {"action": S.action(a), "case": S.case(c)}


@router.post("/{case_id}/execute", dependencies=[Depends(ai_limiter)])
def execute(case_id: str, body: ApproveIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    return approve(case_id, body, cu, db)


@router.post("/actions/{action_id}/reject")
def reject(action_id: str, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    a = owned(db, RecoveryAction, action_id, cu.merchant_id)
    if a.status in ("EXECUTED",):
        raise HTTPException(409, "Already executed")
    a.status = "REJECTED"
    audit(db, cu.merchant_id, "user", "action.rejected", f"{cu.user.full_name} rejected {a.action_type.replace('_', ' ').lower()}",
          actor_id=cu.id, entity_type="recovery_action", entity_id=a.id)
    db.commit()
    return {"action": S.action(a)}


class CloseIn(BaseModel):
    outcome: str = Field("CLOSED_UNRECOVERED", pattern="^(CLOSED_UNRECOVERED|CLOSED_ALREADY_PAID)$")
    note: str | None = Field(None, max_length=500)


@router.post("/{case_id}/close")
def close(case_id: str, body: CloseIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    c = owned(db, RecoveryCase, case_id, cu.merchant_id)
    if c.status not in agent.ACTIVE_CASE:
        raise HTTPException(409, f"Case already {c.status}")
    if body.outcome == "CLOSED_ALREADY_PAID" and not agent.policy.order_is_paid(db, c.order_id):
        raise HTTPException(409, "No completed payment exists for this order, so it cannot be closed as paid.")
    c.status, c.closed_at = body.outcome, agent.utcnow()
    for a in db.scalars(select(RecoveryAction).where(RecoveryAction.case_id == c.id, RecoveryAction.status.in_(["PROPOSED", "SCHEDULED", "APPROVED"]))):
        a.status, a.blocked_reason = "CANCELLED", "Case closed by merchant"
    audit(db, cu.merchant_id, "user", "case.closed", f"{cu.user.full_name} closed case {c.order.order_number} as {body.outcome}"
          + (f": {body.note}" if body.note else ""), actor_id=cu.id, entity_type="recovery_case", entity_id=c.id)
    db.commit()
    return {"case": S.case(c)}


@router.post("/{case_id}/reanalyse", dependencies=[Depends(ai_limiter)])
def reanalyse(case_id: str, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    c = owned(db, RecoveryCase, case_id, cu.merchant_id)
    a = agent.analyse_case(db, c)
    if not db.scalar(select(RecoveryAction.id).where(RecoveryAction.case_id == c.id).limit(1)):
        agent.plan_actions(db, c)
    db.commit()
    return {"analysis": S.analysis(a)}


class PreviewIn(BaseModel):
    tone: str = Field("friendly", pattern="^(professional|friendly|urgent)$")


@router.post("/{case_id}/preview-message", dependencies=[Depends(ai_limiter)])
def preview_message(case_id: str, body: PreviewIn, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    c = owned(db, RecoveryCase, case_id, cu.merchant_id)
    s = agent.merchant_settings(db, cu.merchant_id)
    link = agent.ensure_recovery_link(db, c)
    msg = communication.generate(db, c, tone=body.tone, link=link, brand=s.brand_name or cu.user.merchant.name, support_email=s.support_email)
    db.commit()
    return msg | {"customer": {"name": c.customer.name, "email": c.customer.email, "consent": c.customer.recovery_contact_consent}}


# ---------------------------------------------------------------- Trust Firewall log, reconciliation, order cancellation
from ..models import Order, Payment  # noqa: E402

extra = APIRouter(tags=["recovery"])


@extra.get("/api/firewall/blocked")
def firewall_blocked(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    acts = db.scalars(select(RecoveryAction).where(RecoveryAction.merchant_id == cu.merchant_id, RecoveryAction.status == "BLOCKED")
                      .order_by(RecoveryAction.updated_at.desc()).limit(100)).all()
    out = []
    for a in acts:
        c = db.get(RecoveryCase, a.case_id)
        run = db.scalar(select(AgentRun).where(AgentRun.case_id == a.case_id, AgentRun.outcome == "BLOCKED").order_by(AgentRun.created_at.desc()).limit(1))
        out.append(S.action(a) | {"order_number": c.order.order_number, "customer": c.customer.name, "amount_minor": c.amount_minor,
                                  "currency": c.currency, "guardrails": run.guardrails if run else None})
    return {"items": out}


@extra.post("/api/payments/{payment_id}/reconcile")
def reconcile_payment(payment_id: str, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import retry
    p = owned(db, Payment, payment_id, cu.merchant_id)
    res = retry.reconcile(db, p)
    audit(db, cu.merchant_id, "user", "payment.reconcile_requested", f"{cu.user.full_name} reconciled {p.reference}: {res['note']}", actor_id=cu.id)
    db.commit()
    return {"result": res, "payment": S.payment(p)}


class CancelIn(BaseModel):
    reason: str | None = Field(None, max_length=300)


@extra.post("/api/orders/{order_id}/cancel")
def cancel_order(order_id: str, body: CancelIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    o = owned(db, Order, order_id, cu.merchant_id)
    if agent.policy.order_is_paid(db, o.id):
        raise HTTPException(409, "Order is paid — refunds are handled in PayPal, not cancelled here.")
    if agent.policy.order_has_pending(db, o.id):
        raise HTTPException(409, "A PayPal capture is pending for this order; wait for PayPal's result before cancelling.")
    o.status = "CANCELLED"
    for p in db.scalars(select(Payment).where(Payment.order_id == o.id, Payment.status.in_(["CREATED", "APPROVED"]))):
        agent.apply_status(db, p, "CANCELLED", source="SYSTEM", detail={"reason": body.reason or "cancelled by merchant"}, run_agent=False)
    for c in db.scalars(select(RecoveryCase).where(RecoveryCase.order_id == o.id, RecoveryCase.status.in_(agent.ACTIVE_CASE))):
        c.status, c.closed_at = "CLOSED_UNRECOVERED", agent.utcnow()
    agent.cancel_customer_actions(db, o.id, "Order cancelled by merchant")
    for a in db.scalars(select(RecoveryAction).where(RecoveryAction.case_id.in_(select(RecoveryCase.id).where(RecoveryCase.order_id == o.id)),
                                                     RecoveryAction.status.in_(["PROPOSED", "SCHEDULED", "APPROVED"]))):
        a.status, a.blocked_reason = "CANCELLED", "Order cancelled"
    audit(db, cu.merchant_id, "user", "order.cancelled", f"{cu.user.full_name} cancelled order {o.order_number}" + (f": {body.reason}" if body.reason else ""),
          actor_id=cu.id, entity_type="order", entity_id=o.id)
    db.commit()
    return {"order_status": o.status}
