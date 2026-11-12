"""Relational schema (19 tables). Money is stored as integer minor units (cents) to keep calculations exact.

Every tenant-owned row carries merchant_id. The API enforces tenant scoping on every query; the
Supabase migration additionally enables Row Level Security so the browser can never read these tables directly.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> str:
    return uuid.uuid4().hex


def _in(col: str, values) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


# ---- Status vocabularies (validated by CHECK constraints and by the policy engine) ----
DATA_SOURCES = ("PAYPAL_SANDBOX", "PAYPAL_LIVE", "DEMO_SIMULATED")
PAYMENT_STATUSES = ("CREATED", "APPROVED", "AUTHORIZED", "PENDING", "COMPLETED", "DECLINED", "FAILED", "CANCELLED", "EXPIRED",
                    "PARTIALLY_REFUNDED", "REFUNDED", "VOIDED")
ORDER_STATUSES = ("OPEN", "PAID", "CANCELLED", "REFUNDED")
SESSION_STATUSES = ("ACTIVE", "ABANDONED", "CONVERTED", "EXPIRED")
CASE_STATUSES = ("OPEN", "AWAITING_APPROVAL", "IN_PROGRESS", "RECOVERED", "CLOSED_ALREADY_PAID", "CLOSED_UNRECOVERED")
CASE_TYPES = ("FAILED_PAYMENT", "ABANDONED_CHECKOUT", "PENDING_PAYMENT", "APPROVAL_INCOMPLETE")
ACTION_STATUSES = ("PROPOSED", "SCHEDULED", "APPROVED", "EXECUTED", "REJECTED", "BLOCKED", "SKIPPED", "CANCELLED")
FAILURE_CATEGORIES = (
    "INSUFFICIENT_FUNDS", "CARD_DECLINED", "AUTHENTICATION_REQUIRED", "CHECKOUT_ABANDONED", "APPROVAL_INCOMPLETE",
    "CAPTURE_FAILED", "TECHNICAL_ERROR", "CONFIGURATION_ISSUE", "DUPLICATE_ATTEMPT", "UNKNOWN",
)
RECOVERY_MODES = ("AUTOMATIC", "APPROVAL", "MANUAL")
ROLES = ("owner", "admin", "analyst")
PROVIDER_KEYS = ("paypal_sandbox", "paypal_live", "anthropic", "openai", "gemini", "resend", "twilio")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)


class Merchant(TimestampMixin, Base):
    __tablename__ = "merchants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(300))
    industry: Mapped[str | None] = mapped_column(String(100))
    business_type: Mapped[str | None] = mapped_column(String(60))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="ck_users_role"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="owner")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # bump to revoke every session
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merchant: Mapped[Merchant] = relationship()


class MerchantSettings(TimestampMixin, Base):
    __tablename__ = "merchant_settings"
    __table_args__ = (CheckConstraint(_in("recovery_mode", RECOVERY_MODES), name="ck_settings_mode"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), unique=True)
    recovery_mode: Mapped[str] = mapped_column(String(20), default="APPROVAL")
    reminder_intervals_minutes: Mapped[list] = mapped_column(JSON, default=lambda: [30, 1440, 2880])
    max_reminders_per_case: Mapped[int] = mapped_column(Integer, default=3)
    min_minutes_between_messages: Mapped[int] = mapped_column(Integer, default=25)
    abandonment_threshold_minutes: Mapped[int] = mapped_column(Integer, default=30)
    pending_alert_after_minutes: Mapped[int] = mapped_column(Integer, default=60 * 24)
    default_tone: Mapped[str] = mapped_column(String(20), default="friendly")
    brand_name: Mapped[str | None] = mapped_column(String(200))
    support_email: Mapped[str | None] = mapped_column(String(320))
    # automation & safety policy (enforced by the Trust Firewall)
    max_retry_attempts: Mapped[int] = mapped_column(Integer, default=2)
    retry_cooldown_minutes: Mapped[int] = mapped_column(Integer, default=10)
    max_auto_amount_minor: Mapped[int] = mapped_column(Integer, default=50000)
    permitted_auto_actions: Mapped[list] = mapped_column(JSON, default=lambda: ["SEND_REMINDER", "REQUEST_CUSTOMER_ACTION",
        "RECOMMEND_ALTERNATIVE_METHOD", "NOTIFY_MERCHANT", "WAIT_FOR_PENDING", "MANUAL_INVESTIGATION"])
    escalate_after_hours: Mapped[int] = mapped_column(Integer, default=72)
    quiet_hours_start: Mapped[int | None] = mapped_column(Integer, default=21)  # merchant-local hour, None = off
    quiet_hours_end: Mapped[int | None] = mapped_column(Integer, default=8)
    channels: Mapped[list] = mapped_column(JSON, default=lambda: ["email"])
    notify_on: Mapped[list] = mapped_column(JSON, default=lambda: ["recovered", "approval", "blocked", "escalation", "integration", "duplicate"])
    # provider selection
    paypal_environment: Mapped[str] = mapped_column(String(10), default="sandbox")  # sandbox | live
    ai_provider: Mapped[str] = mapped_column(String(20), default="auto")  # auto | anthropic | openai | none


class PayPalConnection(TimestampMixin, Base):
    __tablename__ = "paypal_connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), unique=True)
    environment: Mapped[str] = mapped_column(String(10), default="sandbox")
    status: Mapped[str] = mapped_column(String(20), default="NOT_CONFIGURED")  # CONNECTED | ERROR | NOT_CONFIGURED
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_webhook_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    webhook_id_configured: Mapped[bool] = mapped_column(Boolean, default=False)


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("merchant_id", "email", name="uq_customer_email"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    country: Mapped[str | None] = mapped_column(String(2))
    phone: Mapped[str | None] = mapped_column(String(32))
    marketing_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    # Consent to receive transactional payment-recovery messages about their own orders
    recovery_contact_consent: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    sku: Mapped[str | None] = mapped_column(String(64))
    price_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(_in("status", ORDER_STATUSES), name="ck_orders_status"),
        CheckConstraint("amount_minor > 0", name="ck_orders_amount"),
        UniqueConstraint("merchant_id", "order_number", name="uq_order_number"),
        Index("ix_orders_merchant_status", "merchant_id", "status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id"))
    order_number: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(300))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    data_source: Mapped[str] = mapped_column(String(20), default="DEMO_SIMULATED")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    customer: Mapped[Customer] = relationship()
    product: Mapped[Product | None] = relationship()


class CheckoutSession(TimestampMixin, Base):
    __tablename__ = "checkout_sessions"
    __table_args__ = (
        CheckConstraint(_in("status", SESSION_STATUSES), name="ck_sessions_status"),
        Index("ix_sessions_merchant_status", "merchant_id", "status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    paypal_order_created: Mapped[bool] = mapped_column(Boolean, default=False)
    paypal_order_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    paypal_order_captured: Mapped[bool] = mapped_column(Boolean, default=False)
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_source: Mapped[str] = mapped_column(String(20), default="DEMO_SIMULATED")
    # Recovery link: random token, stored hashed would be stronger; we store a random 32-byte urlsafe token
    recovery_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    recovery_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Payment(TimestampMixin, Base):
    """One payment attempt. A PayPal order id maps to at most one payment row (unique)."""
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(_in("status", PAYMENT_STATUSES), name="ck_payments_status"),
        CheckConstraint(_in("data_source", DATA_SOURCES), name="ck_payments_source"),
        CheckConstraint("amount_minor > 0", name="ck_payments_amount"),
        UniqueConstraint("paypal_order_id", name="uq_payments_paypal_order"),
        UniqueConstraint("paypal_capture_id", name="uq_payments_paypal_capture"),
        Index("ix_payments_merchant_status", "merchant_id", "status"),
        Index("ix_payments_merchant_created", "merchant_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    checkout_session_id: Mapped[str | None] = mapped_column(ForeignKey("checkout_sessions.id"))
    reference: Mapped[str] = mapped_column(String(40), index=True)  # human readable TXN-xxxx
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    payment_method: Mapped[str] = mapped_column(String(40), default="paypal")
    status: Mapped[str] = mapped_column(String(20), default="CREATED")
    data_source: Mapped[str] = mapped_column(String(20), default="DEMO_SIMULATED")
    paypal_order_id: Mapped[str | None] = mapped_column(String(64))
    paypal_capture_id: Mapped[str | None] = mapped_column(String(64))
    paypal_request_id: Mapped[str | None] = mapped_column(String(64))
    status_verified_by: Mapped[str | None] = mapped_column(String(40))  # PAYPAL_API | PAYPAL_WEBHOOK | DEMO_SIMULATOR
    failure_category: Mapped[str | None] = mapped_column(String(40))
    paypal_issue: Mapped[str | None] = mapped_column(String(80))  # raw issue code / status reason from PayPal
    paypal_debug_id: Mapped[str | None] = mapped_column(String(80))
    raw_response: Mapped[dict | None] = mapped_column(JSON)
    risk_level: Mapped[str | None] = mapped_column(String(10))
    recovery_probability: Mapped[float | None] = mapped_column(Float)
    recommended_action: Mapped[str | None] = mapped_column(String(60))
    is_recovery_attempt: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_case_id: Mapped[str | None] = mapped_column(String(32))
    reconcile_required: Mapped[bool] = mapped_column(Boolean, default=False)  # outcome uncertain (timeout/5xx): never assume failure
    refunded_minor: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    order: Mapped[Order] = relationship()
    customer: Mapped[Customer] = relationship()


class PaymentEvent(Base):
    """Lifecycle timeline for a payment (state transitions, API calls, webhook applications)."""
    __tablename__ = "payment_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(30))  # PAYPAL_API | PAYPAL_WEBHOOK | DEMO_SIMULATOR | SYSTEM
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("paypal_event_id", name="uq_webhook_event_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str | None] = mapped_column(ForeignKey("merchants.id", ondelete="SET NULL"), index=True)
    paypal_event_id: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(80))
    verification_status: Mapped[str] = mapped_column(String(20))  # SUCCESS | FAILURE | SKIPPED_NOT_CONFIGURED | DEMO
    processing_status: Mapped[str] = mapped_column(String(20), default="RECEIVED")  # PROCESSED | IGNORED | ERROR
    processing_error: Mapped[str | None] = mapped_column(Text)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecoveryCase(TimestampMixin, Base):
    __tablename__ = "recovery_cases"
    __table_args__ = (
        CheckConstraint(_in("status", CASE_STATUSES), name="ck_cases_status"),
        CheckConstraint(_in("case_type", CASE_TYPES), name="ck_cases_type"),
        Index("ix_cases_merchant_status", "merchant_id", "status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"))
    checkout_session_id: Mapped[str | None] = mapped_column(ForeignKey("checkout_sessions.id"))
    case_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="OPEN")
    failure_category: Mapped[str | None] = mapped_column(String(40))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    recovery_probability: Mapped[float | None] = mapped_column(Float)
    priority_score: Mapped[float | None] = mapped_column(Float)
    priority_reason: Mapped[str | None] = mapped_column(Text)
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_payment_id: Mapped[str | None] = mapped_column(String(32))
    recovered_amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_source: Mapped[str] = mapped_column(String(20), default="DEMO_SIMULATED")
    order: Mapped[Order] = relationship()
    customer: Mapped[Customer] = relationship()


class RecoveryAction(TimestampMixin, Base):
    __tablename__ = "recovery_actions"
    __table_args__ = (
        CheckConstraint(_in("status", ACTION_STATUSES), name="ck_actions_status"),
        # Idempotency: the same logical action for a case can only exist once
        UniqueConstraint("case_id", "idempotency_key", name="uq_action_idem"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True)
    action_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED")
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(32))
    result: Mapped[dict | None] = mapped_column(JSON)
    blocked_reason: Mapped[str | None] = mapped_column(Text)


class AIAnalysis(Base):
    __tablename__ = "ai_analyses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("recovery_cases.id", ondelete="CASCADE"))
    failure_category: Mapped[str] = mapped_column(String(40))
    classification_basis: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str] = mapped_column(Text)
    severity: Mapped[int] = mapped_column(Integer)
    recovery_probability: Mapped[float] = mapped_column(Float)
    probability_factors: Mapped[list] = mapped_column(JSON)
    recommendation: Mapped[dict] = mapped_column(JSON)
    customer_action_required: Mapped[bool] = mapped_column(Boolean)
    merchant_action_required: Mapped[bool] = mapped_column(Boolean)
    generated_by: Mapped[str] = mapped_column(String(20))  # llm | rules
    model: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CommunicationLog(Base):
    __tablename__ = "communication_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    channel: Mapped[str] = mapped_column(String(20), default="email")
    tone: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    recovery_link: Mapped[str | None] = mapped_column(String(500))
    # DRAFT | SENT | PREVIEW_ONLY (no provider configured, nothing was sent) | FAILED | TEST_SENT
    delivery_status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    provider: Mapped[str | None] = mapped_column(String(30))
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(String(20), default="rules")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    to_address: Mapped[str | None] = mapped_column(String(320))


class RevenuePrediction(Base):
    __tablename__ = "revenue_predictions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    scenario: Mapped[str] = mapped_column(String(60))
    inputs: Mapped[dict] = mapped_column(JSON)
    at_risk_minor: Mapped[int] = mapped_column(Integer)
    expected_minor: Mapped[int] = mapped_column(Integer)
    low_minor: Mapped[int] = mapped_column(Integer)
    high_minor: Mapped[int] = mapped_column(Integer)
    assumptions: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MerchantAnalytics(Base):
    """Daily snapshot rollup (recomputed from source tables; never hand-entered)."""
    __tablename__ = "merchant_analytics"
    __table_args__ = (UniqueConstraint("merchant_id", "day", name="uq_analytics_day"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    completed_minor: Mapped[int] = mapped_column(Integer, default=0)
    recovered_minor: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str | None] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    actor_type: Mapped[str] = mapped_column(String(20))  # user | agent | system | paypal | customer
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # info | success | warning | danger
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(200))
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AgentRun(Base):
    """Explainable trace of one agent decision: every pipeline step and every guardrail check, as executed."""
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True)
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"))
    trigger: Mapped[str] = mapped_column(String(200))
    steps: Mapped[list] = mapped_column(JSON)
    guardrails: Mapped[list] = mapped_column(JSON)
    rail: Mapped[dict | None] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(40))  # EXECUTED | AWAITING_APPROVAL | BLOCKED | RECOMMENDED | WAITING | CLOSED
    summary: Mapped[str] = mapped_column(Text)
    data_source: Mapped[str] = mapped_column(String(20), default="DEMO_SIMULATED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class PayPalInvoice(TimestampMixin, Base):
    """PayPal Invoicing v2 invoice used as a recovery rail."""
    __tablename__ = "paypal_invoices"
    __table_args__ = (UniqueConstraint("paypal_invoice_id", name="uq_paypal_invoice_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    paypal_invoice_id: Mapped[str] = mapped_column(String(64))
    invoice_number: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")  # DRAFT | SENT | PAID | CANCELLED ...
    payer_view_url: Mapped[str | None] = mapped_column(String(500))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    paid_payment_id: Mapped[str | None] = mapped_column(String(32))
    raw: Mapped[dict | None] = mapped_column(JSON)


class IntegrationCredential(TimestampMixin, Base):
    """Merchant-entered provider credentials, encrypted at rest (Fernet). Only masked values ever leave the server."""
    __tablename__ = "integration_credentials"
    __table_args__ = (UniqueConstraint("merchant_id", "provider", name="uq_cred_provider"),
                      CheckConstraint(_in("provider", PROVIDER_KEYS), name="ck_cred_provider"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    masked: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="CONFIGURED")  # CONFIGURED | CONNECTED | ERROR
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class AIUsage(Base):
    """One row per AI provider call (no prompts or secrets stored)."""
    __tablename__ = "ai_usage"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    merchant_id: Mapped[str | None] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(40))
    success: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AuthToken(Base):
    """Password-reset and email-verification tokens (only the SHA-256 hash is stored)."""
    __tablename__ = "auth_tokens"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_auth_token"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # reset | verify
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
