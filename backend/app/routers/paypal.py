from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import serializers as S
from ..config import get_settings
from ..db import get_db
from ..deps import owned
from ..models import CheckoutSession, Customer, Merchant, Order, Payment, PayPalConnection, Product, WebhookEvent
from ..security import CurrentUser, current_user, public_limiter, require_approver, webhook_limiter
from ..services import agent, checkout, providers, webhooks
from ..services.audit import audit, money
from ..services.paypal_client import PayPalError, PayPalNotConfigured

router = APIRouter(tags=["paypal"])


# ------------------------------------------------------------------ integration status
@router.get("/api/paypal/status")
def status(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    s = get_settings()
    env = providers.paypal_environment(db, cu.merchant_id)
    cfg = providers.paypal_config(db, cu.merchant_id)
    conn = db.scalar(select(PayPalConnection).where(PayPalConnection.merchant_id == cu.merchant_id))
    last_wh = db.scalar(select(WebhookEvent).where(WebhookEvent.merchant_id == cu.merchant_id, WebhookEvent.is_simulated.is_(False))
                        .order_by(WebhookEvent.received_at.desc()).limit(1))
    connected = bool(cfg and conn and conn.status == "CONNECTED" and conn.environment == env)
    health = "HEALTHY" if connected and cfg.get("webhook_id") else "DEGRADED" if cfg else "NOT_CONFIGURED"
    ai = providers.ai_config(db, cu.merchant_id)
    base = s.public_api_url.rstrip("/")
    return {
        "environment": env, "base_url": cfg["base_url"] if cfg else (providers.SANDBOX_URL if env == "sandbox" else providers.LIVE_URL),
        "credentials_configured": bool(cfg), "credentials_source": cfg["source"] if cfg else None,
        "client_id_hint": (cfg["client_id"][:6] + "…") if cfg else None,
        "webhook_id_configured": bool(cfg and cfg.get("webhook_id")),
        "webhook_url": f"{base}/api/webhooks/paypal/m/{cu.merchant_id}", "webhook_url_generic": f"{base}/api/webhooks/paypal",
        "connection_status": conn.status if conn and conn.environment == env else "NOT_TESTED" if cfg else "NOT_CONFIGURED",
        "last_success_at": S._dt(conn.last_success_at) if conn else None,
        "last_error": conn.last_error if conn else None, "last_webhook_at": S._dt(last_wh.received_at) if last_wh else None,
        "last_webhook_type": last_wh.event_type if last_wh else None, "last_webhook_verification": last_wh.verification_status if last_wh else None,
        "health": health, "llm_configured": bool(ai), "ai_provider": f"{ai['provider']}:{ai['model']}" if ai else None,
        "email_configured": bool(providers.email_config(db, cu.merchant_id)), "sms_configured": bool(providers.sms_config(db, cu.merchant_id)),
    }


@router.post("/api/paypal/connect")
def test_connection(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    """Test Connection: performs a real OAuth client-credentials call against the selected PayPal environment."""
    env = providers.paypal_environment(db, cu.merchant_id)
    try:
        providers.paypal_for(db, cu.merchant_id).access_token(force=True)
    except PayPalNotConfigured:
        checkout.touch_connection(db, cu.merchant_id, False, "Not configured")
        db.commit()
        return {"ok": False, "message": f"PayPal {env} credentials are not configured. Add them below."}
    except PayPalError as e:
        checkout.touch_connection(db, cu.merchant_id, False, str(e))
        providers.mark(db, cu.merchant_id, f"paypal_{env}", False, str(e))
        audit(db, cu.merchant_id, "user", "paypal.test_failed", f"PayPal {env} connection test failed: {e.name}", actor_id=cu.id)
        db.commit()
        hint = " Check that the Client ID and Secret are from the same app and the same environment (Sandbox vs Live)." if e.http_status == 401 else ""
        return {"ok": False, "message": f"{e}{hint}"}
    checkout.touch_connection(db, cu.merchant_id, True)
    providers.mark(db, cu.merchant_id, f"paypal_{env}", True)
    audit(db, cu.merchant_id, "user", "paypal.test_ok", f"PayPal {env} OAuth connection test succeeded", actor_id=cu.id)
    db.commit()
    return {"ok": True, "message": f"Connected to PayPal {env.title()}: PayPal issued an access token."}


class EnvIn(BaseModel):
    environment: str = Field(pattern="^(sandbox|live)$")
    confirm_live: bool = False


@router.post("/api/paypal/environment")
def set_environment(body: EnvIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    from .integrations import _not_shared_demo
    _not_shared_demo(cu)
    if body.environment == "live" and not body.confirm_live:
        raise HTTPException(400, "Switching to Live affects real money. Confirm explicitly.")
    ms = agent.merchant_settings(db, cu.merchant_id)
    ms.paypal_environment = body.environment
    audit(db, cu.merchant_id, "user", "paypal.environment", f"{cu.user.full_name} switched PayPal to {body.environment.upper()}", actor_id=cu.id)
    db.commit()
    return {"environment": ms.paypal_environment}


@router.get("/api/paypal/client-config")
def client_config():
    """Deprecated global config (public values only). Checkout pages use the per-merchant config in /api/public/checkout."""
    s = get_settings()
    return {"client_id": s.paypal_client_id or None, "currency": s.paypal_currency, "environment": "sandbox" if s.paypal_is_sandbox else "live",
            "enabled": s.paypal_configured}


# ------------------------------------------------------------------ merchant-side test checkout
class TestCheckoutIn(BaseModel):
    product_id: str | None = None
    customer_email: str | None = Field(None, max_length=320)
    customer_name: str | None = Field(None, max_length=200)
    amount_minor: int | None = Field(None, gt=0, le=1_000_000)


@router.post("/api/paypal/test-checkout")
def create_test_checkout(body: TestCheckoutIn, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    """Creates a new order + checkout session and returns a customer checkout link (/pay/<token>).
    The order is labelled PAYPAL_SANDBOX when PayPal is configured, otherwise DEMO_SIMULATED."""
    s = get_settings()
    mid = cu.merchant_id
    product = owned(db, Product, body.product_id, mid) if body.product_id else db.scalar(select(Product).where(Product.merchant_id == mid).limit(1))
    email = (body.customer_email or "sandbox.buyer@example.com").lower()
    cust = db.scalar(select(Customer).where(Customer.merchant_id == mid, Customer.email == email))
    if not cust:
        cust = Customer(merchant_id=mid, name=body.customer_name or "Sandbox Buyer", email=email, recovery_contact_consent=True)
        db.add(cust)
        db.flush()
    amount = body.amount_minor or (product.price_minor if product else 4900)
    source = checkout.paypal_source(db, mid) if providers.paypal_config(db, mid) else "DEMO_SIMULATED"
    order = Order(merchant_id=mid, customer_id=cust.id, product_id=product.id if product else None,
                  order_number=f"NW-{agent.secrets.token_hex(3).upper()}", description=product.name if product else "Test order",
                  amount_minor=amount, data_source=source)
    db.add(order)
    db.flush()
    sess = CheckoutSession(merchant_id=mid, order_id=order.id, customer_id=cust.id, status="ACTIVE", data_source=source)
    db.add(sess)
    db.flush()
    link = agent.session_link(sess)
    audit(db, mid, "user", "checkout.test_created", f"Created {'sandbox' if source != 'DEMO_SIMULATED' else 'demo'} checkout {order.order_number} for {money(amount)}",
          actor_id=cu.id, entity_type="order", entity_id=order.id)
    db.commit()
    return {"order_id": order.id, "order_number": order.order_number, "checkout_url": link, "data_source": source}


@router.post("/api/paypal/create-order")
def create_order_merchant(body: dict, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    order = owned(db, Order, str(body.get("order_id", "")), cu.merchant_id)
    sess = db.scalar(select(CheckoutSession).where(CheckoutSession.order_id == order.id).order_by(CheckoutSession.created_at.desc()).limit(1))
    return checkout.create_paypal_order(db, order, sess)


@router.post("/api/paypal/capture-order")
def capture_order_merchant(body: dict, cu: CurrentUser = Depends(require_approver), db: Session = Depends(get_db)):
    p = checkout.capture_paypal_order(db, str(body.get("paypal_order_id", "")), merchant_id=cu.merchant_id)
    return {"payment": S.payment(p)}


# ------------------------------------------------------------------ public customer checkout (/pay/<token>)
def _session_by_token(db: Session, token: str) -> CheckoutSession:
    if not token or len(token) > 64:
        raise HTTPException(404, "Checkout link not found")
    sess = db.scalar(select(CheckoutSession).where(CheckoutSession.recovery_token == token))
    if not sess:
        raise HTTPException(404, "Checkout link not found")
    if agent.aware(sess.recovery_token_expires_at) and agent.aware(sess.recovery_token_expires_at) < agent.utcnow():
        raise HTTPException(410, "This checkout link has expired. Please contact the store for a new one.")
    return sess


def _active_case_id(db, order_id):
    c = db.scalar(select(agent.RecoveryCase).where(agent.RecoveryCase.order_id == order_id, agent.RecoveryCase.status.in_(agent.ACTIVE_CASE)))
    return c.id if c else None


@router.get("/api/public/checkout/{token}", dependencies=[Depends(public_limiter)])
def public_checkout(token: str, db: Session = Depends(get_db)):
    sess = _session_by_token(db, token)
    order, m = db.get(Order, sess.order_id), db.get(Merchant, sess.merchant_id)
    settings = agent.merchant_settings(db, m.id)
    cust = db.get(Customer, order.customer_id)
    paid = agent.policy.order_is_paid(db, order.id)
    cfg = providers.paypal_config(db, m.id)
    return {"merchant": {"name": settings.brand_name or m.name, "support_email": settings.support_email, "is_demo": m.is_demo},
            "order": {"order_number": order.order_number, "description": order.description, "amount_minor": order.amount_minor,
                      "currency": order.currency, "status": "PAID" if paid else order.status, "data_source": order.data_source},
            "customer_first_name": cust.name.split()[0], "paid": paid, "pending": agent.policy.order_has_pending(db, order.id),
            "paypal": {"enabled": bool(cfg), "client_id": cfg["client_id"] if cfg else None, "currency": order.currency,
                       "environment": cfg["environment"] if cfg else None},
            "simulator_allowed": order.data_source == "DEMO_SIMULATED"}


@router.post("/api/public/checkout/{token}/create-order", dependencies=[Depends(public_limiter)])
def public_create_order(token: str, db: Session = Depends(get_db)):
    sess = _session_by_token(db, token)
    order = db.get(Order, sess.order_id)
    sess.last_activity_at = agent.utcnow()
    if sess.status == "ABANDONED":
        sess.status = "ACTIVE"
    res = checkout.create_paypal_order(db, order, sess, recovery_case_id=_active_case_id(db, order.id))
    return {"id": res["paypal_order_id"], "approve_url": res.get("approve_url"), "reused": res["reused"]}


class CaptureIn(BaseModel):
    paypal_order_id: str = Field(min_length=5, max_length=64)


@router.post("/api/public/checkout/{token}/capture", dependencies=[Depends(public_limiter)])
def public_capture(token: str, body: CaptureIn, db: Session = Depends(get_db)):
    sess = _session_by_token(db, token)
    p = db.scalar(select(Payment).where(Payment.paypal_order_id == body.paypal_order_id, Payment.order_id == sess.order_id))
    if not p:
        raise HTTPException(404, "This PayPal order does not belong to this checkout")
    sess.paypal_order_approved = True
    p = checkout.capture_paypal_order(db, body.paypal_order_id, merchant_id=sess.merchant_id)
    return {"status": p.status, "reference": p.reference, "verified_by": p.status_verified_by,
            "message": {"COMPLETED": "Payment confirmed by PayPal. Thank you!", "PENDING": "PayPal is reviewing this payment. You will be notified once it completes — please do not pay again.",
                        "DECLINED": "PayPal declined this payment. You can try again with a different funding source."}.get(p.status, "Payment status: " + p.status)}


class SimIn(BaseModel):
    outcome: str = Field("complete", pattern="^(complete|pending|decline)$")


@router.post("/api/public/checkout/{token}/simulate", dependencies=[Depends(public_limiter)])
def public_simulate(token: str, body: SimIn, db: Session = Depends(get_db)):
    sess = _session_by_token(db, token)
    order = db.get(Order, sess.order_id)
    p = checkout.simulate_customer_payment(db, order, sess, body.outcome)
    return {"status": p.status, "reference": p.reference, "verified_by": p.status_verified_by, "simulated": True}


class ActivityIn(BaseModel):
    c: str | None = Field(None, max_length=32)


@router.post("/api/public/checkout/{token}/activity", dependencies=[Depends(public_limiter)])
def public_activity(token: str, body: ActivityIn | None = None, db: Session = Depends(get_db)):
    from ..models import CommunicationLog
    sess = _session_by_token(db, token)
    sess.last_activity_at = agent.utcnow()
    if body and body.c:
        log = db.get(CommunicationLog, body.c)
        if log and log.merchant_id == sess.merchant_id and not log.clicked_at:
            case = db.get(agent.RecoveryCase, log.case_id) if log.case_id else None
            if case and case.order_id == sess.order_id:
                log.clicked_at = agent.utcnow()
                audit(db, sess.merchant_id, "customer", "message.clicked", f"Customer opened the recovery link ({log.channel})",
                      entity_type="communication", entity_id=log.id)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ webhooks
@router.post("/api/webhooks/paypal", dependencies=[Depends(webhook_limiter)])
async def paypal_webhook(request: Request, db: Session = Depends(get_db)):
    return await _webhook(request, db, None)


@router.post("/api/webhooks/paypal/m/{merchant_id}", dependencies=[Depends(webhook_limiter)])
async def paypal_webhook_merchant(merchant_id: str, request: Request, db: Session = Depends(get_db)):
    if not db.get(Merchant, merchant_id[:32]):
        return JSONResponse({"error": "unknown merchant"}, status_code=404)
    return await _webhook(request, db, merchant_id[:32])


async def _webhook(request: Request, db: Session, merchant_id: str | None):
    raw = await request.body()
    if len(raw) > 512_000:
        return JSONResponse({"error": "payload too large"}, status_code=413)
    try:
        result = webhooks.ingest(db, dict(request.headers), raw, merchant_hint=merchant_id)
    except webhooks.WebhookRejected as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return result


@router.get("/api/webhooks/events")
def list_webhooks(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(WebhookEvent).where(WebhookEvent.merchant_id == cu.merchant_id).order_by(WebhookEvent.received_at.desc()).limit(50)).all()
    return {"items": [S.webhook(w) for w in rows]}
