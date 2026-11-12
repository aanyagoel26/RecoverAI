"""Analytics, simulator, Copilot, agent control, communications, notifications, audit."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import serializers as S
from ..db import get_db
from ..deps import owned
from ..models import AuditLog, CommunicationLog, Customer, Notification, Payment, RecoveryAction, RecoveryCase
from ..security import CurrentUser, ai_limiter, current_user, require_approver
from ..services import agent, analytics, communication, copilot
from ..services.audit import audit

router = APIRouter(tags=["insights"])


def _win(days: int | None, start: str | None, end: str | None):
    if start and len(start) > 10 or end and len(end or "") > 10:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    return {"days": days, "start": start, "end": end}


# ---------------------------------------------------------------- analytics
@router.get("/api/analytics/overview")
def overview(days: int | None = Query(30, ge=0, le=365), start: str | None = None, end: str | None = None, source: str | None = None,
             cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return analytics.overview(db, cu.merchant_id, source=source, **_win(days, start, end))


@router.get("/api/analytics/revenue")
def revenue(days: int | None = Query(30, ge=0, le=365), start: str | None = None, end: str | None = None,
            cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return {"series": analytics.timeseries(db, cu.merchant_id, **_win(days, start, end))}


@router.get("/api/analytics/breakdowns")
def breakdowns(days: int | None = Query(30, ge=0, le=365), start: str | None = None, end: str | None = None,
               cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return analytics.breakdowns(db, cu.merchant_id, **_win(days, start, end))


@router.get("/api/analytics/export.csv")
def export_csv(kind: str = Query("payments", pattern="^(payments|cases|daily)$"), days: int | None = Query(30, ge=0, le=365),
               start: str | None = None, end: str | None = None, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    s, e = analytics.window(days, start, end)
    if kind == "daily":
        rows = analytics.timeseries(db, cu.merchant_id, **_win(days, start, end))
        w.writerow(["day", "attempts", "completed", "failed", "volume", "recovered", "at_risk_opened"])
        for r in rows:
            w.writerow([r["day"], r["attempts"], r["completed"], r["failed"], f"{r['volume_minor'] / 100:.2f}", f"{r['recovered_minor'] / 100:.2f}",
                        f"{r['at_risk_opened_minor'] / 100:.2f}"])
    elif kind == "cases":
        w.writerow(["case_id", "order", "customer", "type", "status", "category", "amount", "currency", "probability", "recovered_amount", "data_source", "created_at"])
        for c in db.scalars(select(RecoveryCase).where(RecoveryCase.merchant_id == cu.merchant_id)):
            if s <= analytics._aware(c.created_at) <= e:
                w.writerow([c.id, c.order.order_number, c.customer.name, c.case_type, c.status, c.failure_category, f"{c.amount_minor / 100:.2f}",
                            c.currency, c.recovery_probability, f"{c.recovered_amount_minor / 100:.2f}", c.data_source, c.created_at.isoformat()])
    else:
        w.writerow(["reference", "order", "customer", "amount", "currency", "status", "failure_category", "paypal_issue", "data_source",
                    "verified_by", "paypal_order_id", "created_at"])
        for p in db.scalars(select(Payment).where(Payment.merchant_id == cu.merchant_id).order_by(Payment.created_at)):
            if s <= analytics._aware(p.created_at) <= e:
                w.writerow([p.reference, p.order.order_number, p.customer.name, f"{p.amount_minor / 100:.2f}", p.currency, p.status,
                            p.failure_category, p.paypal_issue, p.data_source, p.status_verified_by, p.paypal_order_id, p.created_at.isoformat()])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="recoverai-{kind}.csv"'})


class SimIn(BaseModel):
    scenarios: dict | None = None
    assumptions: dict | None = None
    at_risk_override_minor: int | None = Field(None, gt=0, le=10_000_000_000)


@router.post("/api/simulator/run")
def run_simulator(body: SimIn, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    if body.assumptions:
        for k, v in body.assumptions.items():
            if not isinstance(v, (int, float)) or not 0 <= v <= 1:
                raise HTTPException(400, f"Assumption {k} must be a number between 0 and 1")
    return analytics.simulate(db, cu.merchant_id, scenarios=body.scenarios, assumptions=body.assumptions,
                              at_risk_override_minor=body.at_risk_override_minor)


# ---------------------------------------------------------------- copilot
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[dict] = Field(default_factory=list, max_length=20)


@router.post("/api/ai/chat", dependencies=[Depends(ai_limiter)])
def chat(body: ChatIn, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    hist = [{"role": h.get("role"), "content": str(h.get("content", ""))[:2000]} for h in body.history if h.get("role") in ("user", "assistant")]
    return copilot.chat(db, cu.merchant_id, cu.id, hist, body.message)


# ---------------------------------------------------------------- agent control centre
@router.get("/api/agent/activity")
def agent_activity(limit: int = Query(40, le=200), actor: str | None = None, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    q = select(AuditLog).where(AuditLog.merchant_id == cu.merchant_id)
    q = q.where(AuditLog.actor_type == actor) if actor else q.where(AuditLog.actor_type.in_(["agent", "paypal", "system"]))
    rows = db.scalars(q.order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return {"items": [S.audit_row(a) for a in rows]}


@router.get("/api/agent/approvals")
def approvals(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    acts = db.scalars(select(RecoveryAction).where(RecoveryAction.merchant_id == cu.merchant_id, RecoveryAction.status == "PROPOSED")
                      .order_by(RecoveryAction.created_at.desc())).all()
    out = []
    for a in acts:
        c = db.get(RecoveryCase, a.case_id)
        out.append(S.action(a) | {"case": S.case(c)})
    return {"items": out}


@router.get("/api/agent/actions")
def actions(status: str | None = None, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    q = select(RecoveryAction).where(RecoveryAction.merchant_id == cu.merchant_id)
    if status:
        q = q.where(RecoveryAction.status == status)
    acts = db.scalars(q.order_by(RecoveryAction.updated_at.desc()).limit(100)).all()
    return {"items": [S.action(a) | {"order_number": db.get(RecoveryCase, a.case_id).order.order_number} for a in acts]}


@router.post("/api/agent/run")
def run_agent(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    summary = agent.tick(db, cu.merchant_id)
    db.commit()
    return {"summary": summary}


# ---------------------------------------------------------------- communications
@router.get("/api/communications")
def comms(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(CommunicationLog).where(CommunicationLog.merchant_id == cu.merchant_id).order_by(CommunicationLog.created_at.desc()).limit(100)).all()
    custs = {c.id: c for c in db.scalars(select(Customer).where(Customer.merchant_id == cu.merchant_id))}
    return {"items": [S.comm(m, custs.get(m.customer_id)) for m in rows]}


class TestMsgIn(BaseModel):
    case_id: str
    tone: str = Field("friendly", pattern="^(professional|friendly|urgent)$")
    subject: str | None = Field(None, max_length=300)
    body: str | None = Field(None, max_length=5000)


@router.post("/api/communications/test", dependencies=[Depends(ai_limiter)])
def send_test(body: TestMsgIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    """Sends the message to the signed-in merchant's own email (never to the customer)."""
    c = owned(db, RecoveryCase, body.case_id, cu.merchant_id)
    s = agent.merchant_settings(db, cu.merchant_id)
    link = agent.ensure_recovery_link(db, c)
    msg = communication.generate(db, c, tone=body.tone, link=link, brand=s.brand_name or cu.user.merchant.name, support_email=s.support_email)
    log = CommunicationLog(merchant_id=cu.merchant_id, case_id=None, customer_id=c.customer_id, tone=body.tone, channel="email-test",
                           subject="[TEST] " + (body.subject or msg["subject"]), body=body.body or msg["body"], recovery_link=link,
                           generated_by=msg["generated_by"])
    db.add(log)
    db.flush()
    communication.deliver(db, log, cu.user.email, test=True)
    audit(db, cu.merchant_id, "user", "message.test", f"Test message to {cu.user.email}: {log.delivery_status}", actor_id=cu.id)
    db.commit()
    return S.comm(log)


# ---------------------------------------------------------------- notifications & audit
@router.get("/api/notifications")
def notifications(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Notification).where(Notification.merchant_id == cu.merchant_id).order_by(Notification.created_at.desc()).limit(60)).all()
    return {"items": [S.notification(n) for n in rows], "unread": sum(1 for n in rows if not n.read)}


@router.post("/api/notifications/read")
def mark_read(body: dict, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    ids = body.get("ids")
    q = select(Notification).where(Notification.merchant_id == cu.merchant_id, Notification.read.is_(False))
    if ids:
        q = q.where(Notification.id.in_([str(i) for i in ids][:200]))
    for n in db.scalars(q):
        n.read = True
    db.commit()
    return {"ok": True}


@router.get("/api/audit")
def audit_logs(actor: str | None = None, q: str | None = Query(None, max_length=80), limit: int = Query(100, le=500),
               cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    query = select(AuditLog).where(AuditLog.merchant_id == cu.merchant_id)
    if actor:
        query = query.where(AuditLog.actor_type == actor)
    if q:
        query = query.where(AuditLog.summary.ilike(f"%{q}%"))
    rows = db.scalars(query.order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return {"items": [S.audit_row(a) for a in rows]}


# ---------------------------------------------------------------- Mission Control: agent runs + Failure Lab
from ..models import AgentRun, PayPalInvoice  # noqa: E402
from ..services import lab  # noqa: E402


@router.get("/api/agent/runs")
def agent_runs(case_id: str | None = None, limit: int = Query(30, le=100), cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    q = select(AgentRun).where(AgentRun.merchant_id == cu.merchant_id)
    if case_id:
        q = q.where(AgentRun.case_id == case_id)
    rows = db.scalars(q.order_by(AgentRun.created_at.desc()).limit(limit)).all()
    return {"items": [S.run(r) | {"customer": db.get(RecoveryCase, r.case_id).customer.name if r.case_id else None,
                                  "amount_minor": db.get(RecoveryCase, r.case_id).amount_minor if r.case_id else None} for r in rows]}


@router.get("/api/lab/scenarios")
def lab_scenarios(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import providers
    real = bool(providers.paypal_config(db, cu.merchant_id, "sandbox")) and providers.paypal_environment(db, cu.merchant_id) == "sandbox"
    return {"real_paypal": real,
            "items": [{"key": k, **{x: v[x] for x in ("label", "issue", "amount_minor", "item", "expect_rail", "story")}} for k, v in lab.SCENARIOS.items()]}


class LabIn(BaseModel):
    scenario: str = Field(pattern="^[a-z_]{3,30}$")
    customer_name: str | None = Field(None, max_length=200)
    customer_email: str | None = Field(None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    country: str = Field("US", pattern="^[A-Za-z]{2}$")


@router.post("/api/lab/run", dependencies=[Depends(ai_limiter)])
def lab_run(body: LabIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    return lab.run(db, cu.merchant_id, body.scenario, customer_name=body.customer_name, customer_email=body.customer_email,
                   country=body.country, actor_id=cu.id)


@router.post("/api/recovery/{case_id}/invoice/sync")
def invoice_sync(case_id: str, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import invoices
    from ..services.paypal_client import PayPalError, PayPalNotConfigured
    c = owned(db, RecoveryCase, case_id, cu.merchant_id)
    inv = db.scalar(select(PayPalInvoice).where(PayPalInvoice.case_id == c.id).order_by(PayPalInvoice.created_at.desc()).limit(1))
    if not inv:
        raise HTTPException(404, "No PayPal invoice on this case")
    try:
        invoices.sync(db, inv)
    except (PayPalError, PayPalNotConfigured) as e:
        raise HTTPException(502, str(e))
    return {"invoice": S.invoice(inv), "case_status": c.status}


# ---------------------------------------------------------------- Failure Intelligence
from ..services import intelligence  # noqa: E402


@router.get("/api/intelligence")
def failure_intelligence(days: int | None = Query(30, ge=0, le=365), start: str | None = None, end: str | None = None,
                         category: str | None = Query(None, max_length=40), group: str | None = Query(None, max_length=20),
                         source: str | None = Query(None, max_length=20), method: str | None = Query(None, max_length=30),
                         cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return intelligence.compute(db, cu.merchant_id, category=category, group=group, source=source, method=method, **_win(days, start, end))


@router.post("/api/intelligence/ai-summary", dependencies=[Depends(ai_limiter)])
def failure_ai_summary(days: int | None = Query(30, ge=0, le=365), cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    r = intelligence.compute(db, cu.merchant_id, days=days)
    out = intelligence.ai_summary(db, cu.merchant_id, r)
    db.commit()
    return out


@router.get("/api/insights")
def dashboard_insights(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    r = intelligence.compute(db, cu.merchant_id, days=30)
    return {"items": r["insights"][:4]}
