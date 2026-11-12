"""RecoverAI Copilot: answers merchant questions using tool calls over the merchant's own data.

With an LLM key the model chooses tools (structured tool use). Without one, a deterministic intent router calls the
same tools. Either way every number in the answer comes from a tool result, i.e. from the database.
Customer-facing sends are never executed by the Copilot: it can only prepare them for one-click merchant approval.
"""
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CheckoutSession, Customer, Order, Payment, RecoveryAction, RecoveryCase
from . import agent, analytics, communication, llm
from .audit import audit, money

TOOLS = [
    {"name": "get_payment_analytics", "description": "Payment and recovery KPIs for the last N days (0 = today).",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer", "default": 7}}}},
    {"name": "get_failed_payments", "description": "List failed/declined payments, optionally by failure category.",
     "input_schema": {"type": "object", "properties": {"category": {"type": "string"}, "limit": {"type": "integer", "default": 10}}}},
    {"name": "analyse_transaction", "description": "Run failure analysis for a payment by reference like TXN-ABC123.",
     "input_schema": {"type": "object", "properties": {"reference": {"type": "string"}}, "required": ["reference"]}},
    {"name": "get_recovery_cases", "description": "Open recovery cases ranked by priority.",
     "input_schema": {"type": "object", "properties": {"status": {"type": "string"}, "limit": {"type": "integer", "default": 10}}}},
    {"name": "calculate_recovery_probability", "description": "Recovery probability and its factors for a payment reference.",
     "input_schema": {"type": "object", "properties": {"reference": {"type": "string"}}, "required": ["reference"]}},
    {"name": "generate_recovery_message", "description": "Draft (not send) a recovery email for a case.",
     "input_schema": {"type": "object", "properties": {"case_id": {"type": "string"}, "tone": {"type": "string", "enum": list(communication.TONES)}}, "required": ["case_id"]}},
    {"name": "create_recovery_case", "description": "Open a recovery case for a failed payment reference.",
     "input_schema": {"type": "object", "properties": {"reference": {"type": "string"}}, "required": ["reference"]}},
    {"name": "send_approved_recovery_message", "description": "Prepare a pending reminder for the merchant to approve with one click. Never sends by itself.",
     "input_schema": {"type": "object", "properties": {"case_id": {"type": "string"}}, "required": ["case_id"]}},
    {"name": "get_revenue_forecast", "description": "Revenue-rescue estimates for standard strategies (estimates, not actuals).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_pending_approvals", "description": "Recovery actions waiting for the merchant's approval.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_blocked_actions", "description": "Actions the Trust Firewall blocked, with the reason (e.g. why a payment was not retried).",
     "input_schema": {"type": "object", "properties": {"reference": {"type": "string"}}}},
    {"name": "get_abandoned_checkouts", "description": "Customers whose checkout was abandoned.",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}}},
]


def _pay(db, mid, ref):
    return db.scalar(select(Payment).where(Payment.merchant_id == mid, Payment.reference == (ref or "").upper().strip()))


def run_tool(db: Session, mid: str, name: str, args: dict) -> dict:
    if name == "get_payment_analytics":
        o = analytics.overview(db, mid, days=int(args.get("days", 7)))
        b = analytics.breakdowns(db, mid, days=int(args.get("days", 7)))
        return {k: o[k] for k in ("total_attempts", "successful", "failed", "pending", "abandoned_sessions", "recovered_count",
                                  "revenue_recovered_minor", "revenue_at_risk_minor", "success_rate", "recovery_conversion_rate",
                                  "currency", "open_cases")} | {"failure_categories": b["failure_categories"], "strategies": b["strategies"], "days": args.get("days", 7)}
    if name == "get_failed_payments":
        q = select(Payment).where(Payment.merchant_id == mid, Payment.status.in_(["DECLINED", "FAILED"]))
        if args.get("category"):
            q = q.where(Payment.failure_category == args["category"].upper())
        rows = db.scalars(q.order_by(Payment.created_at.desc()).limit(int(args.get("limit", 10)))).all()
        return {"payments": [{"reference": p.reference, "customer": p.customer.name, "amount": money(p.amount_minor, p.currency),
                              "category": p.failure_category, "recovery_probability": p.recovery_probability, "source": p.data_source} for p in rows]}
    if name in ("analyse_transaction", "calculate_recovery_probability", "create_recovery_case"):
        p = _pay(db, mid, args.get("reference"))
        if not p:
            return {"error": f"No payment with reference {args.get('reference')}"}
        case = agent.ensure_case(db, payment=p)
        if not case:
            return {"result": f"{p.reference}: order already paid — no recovery needed"}
        a = agent.analyse_case(db, case, payment=p)
        db.commit()
        if name == "calculate_recovery_probability":
            return {"reference": p.reference, "probability": a.recovery_probability, "factors": a.probability_factors}
        return {"reference": p.reference, "case_id": case.id, "category": a.failure_category, "explanation": a.explanation,
                "root_cause": a.root_cause, "probability": a.recovery_probability, "recommended_action": a.recommendation["action"],
                "reason": a.recommendation["reason"], "requires_approval": a.recommendation["requires_approval"]}
    if name == "get_recovery_cases":
        q = select(RecoveryCase).where(RecoveryCase.merchant_id == mid)
        q = q.where(RecoveryCase.status == args["status"]) if args.get("status") else q.where(RecoveryCase.status.in_(agent.ACTIVE_CASE))
        rows = sorted(db.scalars(q).all(), key=lambda c: -(c.priority_score or 0))[: int(args.get("limit", 10))]
        return {"cases": [{"case_id": c.id, "customer": c.customer.name, "order": c.order.order_number, "amount": money(c.amount_minor, c.currency),
                           "type": c.case_type, "category": c.failure_category, "probability": c.recovery_probability,
                           "status": c.status, "priority_reason": c.priority_reason} for c in rows]}
    if name == "generate_recovery_message":
        case = db.scalar(select(RecoveryCase).where(RecoveryCase.id == args.get("case_id"), RecoveryCase.merchant_id == mid))
        if not case:
            return {"error": "Unknown case"}
        s = agent.merchant_settings(db, mid)
        link = agent.ensure_recovery_link(db, case)
        db.commit()
        return communication.generate(db, case, tone=args.get("tone", s.default_tone), link=link, brand=s.brand_name or case.order.description,
                                      support_email=s.support_email)
    if name == "send_approved_recovery_message":
        case = db.scalar(select(RecoveryCase).where(RecoveryCase.id == args.get("case_id"), RecoveryCase.merchant_id == mid))
        if not case:
            return {"error": "Unknown case"}
        act = db.scalar(select(RecoveryAction).where(RecoveryAction.case_id == case.id, RecoveryAction.status == "PROPOSED").order_by(RecoveryAction.created_at))
        if not act:
            return {"result": "No reminder is waiting for approval on this case."}
        return {"requires_confirmation": True, "action_id": act.id, "case_id": case.id, "action_type": act.action_type,
                "note": "Merchant must click Approve & send. The Copilot does not send messages on its own."}
    if name == "get_revenue_forecast":
        r = analytics.simulate(db, mid)
        return {"scenarios": [{k: s[k] for k in ("label", "expected_minor", "low_minor", "high_minor", "at_risk_minor")} for s in r["scenarios"]],
                "basis": r["basis"], "disclaimer": r["disclaimer"]}
    if name == "get_pending_approvals":
        acts = db.scalars(select(RecoveryAction).where(RecoveryAction.merchant_id == mid, RecoveryAction.status == "PROPOSED")
                          .order_by(RecoveryAction.created_at.desc()).limit(15)).all()
        out = []
        for a in acts:
            c = db.get(RecoveryCase, a.case_id)
            out.append({"case_id": c.id, "customer": c.customer.name, "amount": money(c.amount_minor, c.currency), "action": a.action_type, "reason": (a.reason or "")[:200]})
        return {"approvals": out}
    if name == "get_blocked_actions":
        q = select(RecoveryAction).where(RecoveryAction.merchant_id == mid, RecoveryAction.status == "BLOCKED")
        if args.get("reference"):
            p = _pay(db, mid, args["reference"])
            if p:
                q = q.where(RecoveryAction.case_id.in_(select(RecoveryCase.id).where(RecoveryCase.order_id == p.order_id)))
        acts = db.scalars(q.order_by(RecoveryAction.updated_at.desc()).limit(10)).all()
        return {"blocked": [{"case_id": a.case_id, "customer": db.get(RecoveryCase, a.case_id).customer.name, "action": a.action_type,
                             "reason": a.blocked_reason} for a in acts]}
    if name == "get_abandoned_checkouts":
        rows = db.scalars(select(CheckoutSession).where(CheckoutSession.merchant_id == mid, CheckoutSession.status == "ABANDONED")
                          .order_by(CheckoutSession.created_at.desc()).limit(int(args.get("limit", 10)))).all()
        out = []
        for s in rows:
            o, c = db.get(Order, s.order_id), db.get(Customer, s.customer_id)
            out.append({"customer": c.name, "email_consent": c.recovery_contact_consent, "order": o.order_number,
                        "amount": money(o.amount_minor, o.currency), "paypal_order_created": s.paypal_order_created})
        return {"abandoned": out}
    return {"error": f"Unknown tool {name}"}


SYSTEM = ("You are RecoverAI Copilot, an assistant for a merchant's PayPal payment recovery. Use tools for every number; "
          "never estimate or invent figures. Amounts ending in _minor are cents — convert to currency. Clearly label "
          "forecasts as estimates. You cannot send messages or move money; for sends, prepare an approval. Be concise, "
          "use short bullet lists where useful. Payment references look like TXN-XXXXXX.")


def chat(db: Session, mid: str, user_id: str, history: list[dict], message: str) -> dict:
    tool_calls: list[dict] = []
    try:
        msgs = [{"role": m["role"], "content": m["content"]} for m in history[-8:] if m.get("role") in ("user", "assistant")]
        msgs.append({"role": "user", "content": message})
        for _ in range(5):
            resp = llm.chat(msgs, system=SYSTEM, tools=TOOLS, max_tokens=1200, db=db, merchant_id=mid, purpose="copilot")
            uses = [b for b in resp.get("content", []) if b.get("type") == "tool_use"]
            if not uses:
                answer = llm.text_of(resp)
                break
            msgs.append({"role": "assistant", "content": resp["content"]})
            results = []
            for u in uses:
                out = run_tool(db, mid, u["name"], u.get("input") or {})
                tool_calls.append({"tool": u["name"], "input": u.get("input"), "output": out})
                results.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(out, default=str)[:8000]})
            msgs.append({"role": "user", "content": results})
        else:
            answer = "I ran several lookups but could not finish. Please try a narrower question."
        generated_by = "llm"
    except llm.LLMUnavailable:
        answer, tool_calls = _rules_answer(db, mid, message)
        generated_by = "rules"
    audit(db, mid, "user", "copilot.query", f"Copilot: {message[:120]}", actor_id=user_id,
          detail={"tools": [t["tool"] for t in tool_calls], "generated_by": generated_by})
    db.commit()
    confirm = next((t["output"] for t in tool_calls if isinstance(t["output"], dict) and t["output"].get("requires_confirmation")), None)
    return {"answer": answer, "tool_calls": tool_calls, "generated_by": generated_by, "confirmation": confirm}


# ---------------------------------------------------------------- deterministic fallback
def _m(minor, cur="USD"):
    return money(int(minor or 0), cur)


def _rules_answer(db: Session, mid: str, q: str) -> tuple[str, list]:
    ql = q.lower()
    calls = []

    def call(name, **args):
        out = run_tool(db, mid, name, args)
        calls.append({"tool": name, "input": args, "output": out})
        return out

    ref = re.search(r"txn-[a-z0-9]+", ql)
    if "approv" in ql:
        out = call("get_pending_approvals")
        if not out["approvals"]:
            return "Nothing is waiting for your approval.", calls
        return "These actions need your approval:\n" + "\n".join(f"- **{a['customer']}** — {a['amount']}: {a['action'].replace('_', ' ').lower()}" for a in out["approvals"]), calls
    if "retr" in ql or "blocked" in ql or "why was" in ql and "not" in ql:
        out = call("get_blocked_actions", reference=ref.group(0) if ref else None)
        if not out["blocked"]:
            return "No actions have been blocked by the Trust Firewall" + (f" for {ref.group(0).upper()}." if ref else "."), calls
        return "The Trust Firewall blocked these actions:\n" + "\n".join(f"- **{b['customer']}** — {b['action'].replace('_', ' ').lower()}: {b['reason']}" for b in out["blocked"]), calls
    if ref:
        out = call("analyse_transaction", reference=ref.group(0))
        if "error" in out or "result" in out:
            return out.get("error") or out["result"], calls
        return (f"**{out['reference']}** — {out['category'].replace('_', ' ').title()}\n\n{out['explanation']}\n\n"
                f"Recovery probability: **{out['probability']:.0%}**\nRecommended: **{out['recommended_action'].replace('_', ' ').title()}** — {out['reason']}"), calls
    days = 0 if "today" in ql else 30 if "month" in ql else 90 if "quarter" in ql else 7
    if any(w in ql for w in ("abandon",)):
        out = call("get_abandoned_checkouts", limit=10)
        if not out["abandoned"]:
            return "No abandoned checkouts right now.", calls
        lines = [f"- {a['customer']} — {a['amount']} ({a['order']}){'' if a['email_consent'] else ' · no contact consent'}" for a in out["abandoned"]]
        return "Customers with abandoned checkouts:\n" + "\n".join(lines), calls
    if any(w in ql for w in ("attention", "priority", "urgent", "plan", "today's pending", "need")):
        out = call("get_recovery_cases", limit=6)
        if not out["cases"]:
            return "Nothing needs attention — no open recovery cases.", calls
        lines = [f"{i + 1}. **{c['customer']}** — {c['amount']} · {(c['category'] or c['type']).replace('_', ' ').lower()} · "
                 f"{(c['probability'] or 0):.0%} likely · {c['priority_reason']}" for i, c in enumerate(out["cases"])]
        head = "Here's today's recovery plan, highest expected value first:" if "plan" in ql else "These cases need attention, ranked by expected recoverable value:"
        return head + "\n" + "\n".join(lines), calls
    if any(w in ql for w in ("forecast", "simulate", "could i recover", "potential")):
        out = call("get_revenue_forecast")
        lines = [f"- {s['label']}: **{_m(s['expected_minor'])}** expected (range {_m(s['low_minor'])}–{_m(s['high_minor'])})" for s in out["scenarios"]]
        return "Estimated recoverable revenue (estimates, not actuals):\n" + "\n".join(lines) + f"\n\n_{out['basis']}_", calls
    out = call("get_payment_analytics", days=days)
    cur = out["currency"]
    label = "today" if days == 0 else f"the last {days} days"
    if any(w in ql for w in ("strategy", "working best")):
        st = sorted(out["strategies"], key=lambda s: -s["rate"])
        if not st:
            return "There's not enough closed-case history yet to compare strategies.", calls
        return "Recovery performance by strategy:\n" + "\n".join(f"- {s['strategy'].replace('_', ' ').title()}: {s['recovered']}/{s['cases']} recovered ({s['rate']:.0%}), {_m(s['recovered_minor'], cur)}" for s in st), calls
    if any(w in ql for w in ("why", "failing", "category", "common", "cause", "decreas")):
        fc = out["failure_categories"]
        if not fc:
            return f"No failed payments in {label}.", calls
        top = fc[0]
        lines = [f"- {c['category'].replace('_', ' ').title()}: {c['count']}" for c in fc]
        sr = f"{out['success_rate']:.0%}" if out["success_rate"] is not None else "n/a"
        return (f"In {label}, {out['failed']} of {out['total_attempts']} payment attempts failed (success rate {sr}). "
                f"The most common cause is **{top['category'].replace('_', ' ').lower()}** ({top['count']}).\n" + "\n".join(lines)), calls
    sr = f"{out['success_rate']:.0%}" if out["success_rate"] is not None else "n/a"
    return (f"In {label}: RecoverAI recovered **{_m(out['revenue_recovered_minor'], cur)}** across {out['recovered_count']} case(s). "
            f"{_m(out['revenue_at_risk_minor'], cur)} is still at risk in {out['open_cases']} open case(s). "
            f"Payment success rate {sr}; {out['failed']} failed, {out['pending']} pending, {out['abandoned_sessions']} abandoned checkouts."), calls
