from .models import (AIAnalysis, AuditLog, CheckoutSession, CommunicationLog, Customer, Notification, Payment, PaymentEvent,
                     RecoveryAction, RecoveryCase, WebhookEvent)


def _dt(d):
    return d.isoformat() if d else None


def payment(p: Payment) -> dict:
    return {
        "id": p.id, "reference": p.reference, "order_id": p.order_id, "order_number": p.order.order_number if p.order else None,
        "description": p.order.description if p.order else None,
        "customer": {"id": p.customer_id, "name": p.customer.name, "email": p.customer.email} if p.customer else None,
        "amount_minor": p.amount_minor, "currency": p.currency, "payment_method": p.payment_method, "status": p.status,
        "data_source": p.data_source, "paypal_order_id": p.paypal_order_id, "paypal_capture_id": p.paypal_capture_id,
        "status_verified_by": p.status_verified_by, "failure_category": p.failure_category, "paypal_issue": p.paypal_issue,
        "paypal_debug_id": p.paypal_debug_id, "risk_level": p.risk_level, "recovery_probability": p.recovery_probability,
        "recommended_action": p.recommended_action, "is_recovery_attempt": p.is_recovery_attempt, "recovery_case_id": p.recovery_case_id,
        "created_at": _dt(p.created_at), "updated_at": _dt(p.updated_at), "completed_at": _dt(p.completed_at),
        "reconcile_required": bool(p.reconcile_required), "refunded_minor": p.refunded_minor or 0,
        "order_status": p.order.status if p.order else None,
    }


def event(e: PaymentEvent) -> dict:
    return {"id": e.id, "event_type": e.event_type, "from_status": e.from_status, "to_status": e.to_status, "source": e.source,
            "detail": e.detail, "created_at": _dt(e.created_at)}


def analysis(a: AIAnalysis) -> dict:
    return {"id": a.id, "payment_id": a.payment_id, "case_id": a.case_id, "failure_category": a.failure_category,
            "classification_basis": a.classification_basis, "explanation": a.explanation, "root_cause": a.root_cause,
            "severity": a.severity, "recovery_probability": a.recovery_probability, "probability_factors": a.probability_factors,
            "recommendation": a.recommendation, "customer_action_required": a.customer_action_required,
            "merchant_action_required": a.merchant_action_required, "generated_by": a.generated_by, "model": a.model,
            "created_at": _dt(a.created_at)}


def action(a: RecoveryAction) -> dict:
    return {"id": a.id, "case_id": a.case_id, "action_type": a.action_type, "status": a.status, "requires_approval": a.requires_approval,
            "reason": a.reason, "payload": a.payload, "scheduled_for": _dt(a.scheduled_for), "executed_at": _dt(a.executed_at),
            "result": a.result, "blocked_reason": a.blocked_reason, "created_at": _dt(a.created_at)}


def case(c: RecoveryCase, actions: list | None = None) -> dict:
    return {"id": c.id, "order_id": c.order_id, "order_number": c.order.order_number, "description": c.order.description,
            "customer": {"id": c.customer_id, "name": c.customer.name, "email": c.customer.email,
                         "consent": c.customer.recovery_contact_consent},
            "payment_id": c.payment_id, "case_type": c.case_type, "status": c.status, "failure_category": c.failure_category,
            "amount_minor": c.amount_minor, "currency": c.currency, "recovery_probability": c.recovery_probability,
            "priority_score": c.priority_score, "priority_reason": c.priority_reason, "reminders_sent": c.reminders_sent,
            "last_contacted_at": _dt(c.last_contacted_at), "recovered_amount_minor": c.recovered_amount_minor,
            "recovered_payment_id": c.recovered_payment_id, "recovered_at": _dt(c.recovered_at), "closed_at": _dt(c.closed_at),
            "data_source": c.data_source, "created_at": _dt(c.created_at), "updated_at": _dt(c.updated_at),
            "actions": [action(a) for a in actions] if actions is not None else None}


def comm(m: CommunicationLog, customer: Customer | None = None) -> dict:
    return {"id": m.id, "case_id": m.case_id, "customer_id": m.customer_id, "customer_name": customer.name if customer else None,
            "customer_email": customer.email if customer else None, "channel": m.channel, "tone": m.tone, "subject": m.subject,
            "body": m.body, "recovery_link": m.recovery_link, "delivery_status": m.delivery_status, "provider": m.provider,
            "error": m.error, "generated_by": m.generated_by, "created_at": _dt(m.created_at), "sent_at": _dt(m.sent_at),
            "delivered_at": _dt(m.delivered_at), "clicked_at": _dt(m.clicked_at), "to_address": m.to_address}


def audit_row(a: AuditLog) -> dict:
    return {"id": a.id, "actor_type": a.actor_type, "actor_id": a.actor_id, "action": a.action, "entity_type": a.entity_type,
            "entity_id": a.entity_id, "summary": a.summary, "detail": a.detail, "created_at": _dt(a.created_at)}


def notification(n: Notification) -> dict:
    return {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link, "read": n.read, "created_at": _dt(n.created_at)}


def webhook(w: WebhookEvent) -> dict:
    return {"id": w.id, "paypal_event_id": w.paypal_event_id, "event_type": w.event_type, "resource_id": w.resource_id,
            "verification_status": w.verification_status, "processing_status": w.processing_status, "processing_error": w.processing_error,
            "is_simulated": w.is_simulated, "received_at": _dt(w.received_at), "processed_at": _dt(w.processed_at)}


def session(s: CheckoutSession) -> dict:
    return {"id": s.id, "order_id": s.order_id, "status": s.status, "last_activity_at": _dt(s.last_activity_at),
            "paypal_order_created": s.paypal_order_created, "paypal_order_approved": s.paypal_order_approved,
            "paypal_order_captured": s.paypal_order_captured, "abandoned_at": _dt(s.abandoned_at), "data_source": s.data_source,
            "created_at": _dt(s.created_at)}


def run(r) -> dict:
    return {"id": r.id, "case_id": r.case_id, "payment_id": r.payment_id, "trigger": r.trigger, "steps": r.steps, "guardrails": r.guardrails,
            "rail": r.rail, "outcome": r.outcome, "summary": r.summary, "data_source": r.data_source, "created_at": _dt(r.created_at)}


def invoice(i) -> dict:
    return {"id": i.id, "case_id": i.case_id, "paypal_invoice_id": i.paypal_invoice_id, "invoice_number": i.invoice_number, "status": i.status,
            "payer_view_url": i.payer_view_url, "amount_minor": i.amount_minor, "currency": i.currency, "paid_payment_id": i.paid_payment_id,
            "created_at": _dt(i.created_at)}
