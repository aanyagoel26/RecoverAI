"""Failure Intelligence: recurring payment problems, computed only from this merchant's records.

Insights are deterministic statements with the numbers that support them (generated_by = "computed").
An optional AI summary may rephrase those facts; it is labelled, and unavailable rather than faked without a provider.
"""
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CheckoutSession, Customer, Payment, RecoveryCase
from .analytics import _aware, window
from .audit import money
from .classifier import CATEGORY_INFO

GROUPS = {
    "authentication": ("AUTHENTICATION_REQUIRED", "APPROVAL_INCOMPLETE"),
    "customer": ("CARD_DECLINED", "INSUFFICIENT_FUNDS", "CHECKOUT_ABANDONED"),
    "technical": ("TECHNICAL_ERROR", "CAPTURE_FAILED"),
    "configuration": ("CONFIGURATION_ISSUE",),
    "duplicate": ("DUPLICATE_ATTEMPT",),
    "unknown": ("UNKNOWN",),
}
GROUP_OF = {c: g for g, cs in GROUPS.items() for c in cs}
FAILED = ("DECLINED", "FAILED")


def compute(db: Session, mid: str, *, days: int | None = 30, start=None, end=None, category: str | None = None,
            group: str | None = None, source: str | None = None, method: str | None = None) -> dict:
    s, e = window(days, start, end)
    pays = [p for p in db.scalars(select(Payment).where(Payment.merchant_id == mid)) if s <= _aware(p.created_at) <= e]
    if source:
        pays = [p for p in pays if p.data_source == source]
    if method:
        pays = [p for p in pays if p.payment_method == method]
    failed = [p for p in pays if p.status in FAILED or (p.status in ("CREATED", "APPROVED") and p.reconcile_required)]
    for p in failed:
        p._cat = p.failure_category or ("TECHNICAL_ERROR" if p.reconcile_required else "UNKNOWN")
    if category:
        failed = [p for p in failed if p._cat == category]
    if group:
        failed = [p for p in failed if GROUP_OF.get(p._cat) == group]

    cats = Counter(p._cat for p in failed)
    groups = Counter(GROUP_OF.get(p._cat, "unknown") for p in failed)
    # daily trend by group
    trend: dict[str, dict] = defaultdict(lambda: {g: 0 for g in GROUPS})
    for p in failed:
        trend[_aware(p.created_at).date().isoformat()][GROUP_OF.get(p._cat, "unknown")] += 1
    trend_rows = [{"day": d, **v} for d, v in sorted(trend.items())]
    # provider error patterns (raw PayPal issue codes)
    patterns: dict[str, dict] = {}
    for p in failed:
        code = p.paypal_issue or "(no code returned)"
        row = patterns.setdefault(code, {"code": code, "count": 0, "amount_minor": 0, "category": p._cat, "last_seen": None, "example": p.reference,
                                          "example_id": p.id, "sources": set()})
        row["count"] += 1
        row["amount_minor"] += p.amount_minor
        row["sources"].add(p.data_source)
        if not row["last_seen"] or _aware(p.created_at).isoformat() > row["last_seen"]:
            row["last_seen"], row["example"], row["example_id"] = _aware(p.created_at).isoformat(), p.reference, p.id
    pattern_rows = sorted(({**r, "sources": sorted(r["sources"])} for r in patterns.values()), key=lambda r: -r["count"])
    # repeat failures
    by_customer = Counter(p.customer_id for p in failed)
    by_order = Counter(p.order_id for p in failed)
    names = {c.id: (c.name, c.email) for c in db.scalars(select(Customer).where(Customer.merchant_id == mid))}
    repeat_customers = [{"customer_id": cid, "customer": names.get(cid, ("?", ""))[0], "failures": n} for cid, n in by_customer.most_common() if n >= 2][:10]
    repeat_orders = sum(1 for n in by_order.values() if n >= 2)
    # recovery outcomes by category (closed cases in window)
    cases = [c for c in db.scalars(select(RecoveryCase).where(RecoveryCase.merchant_id == mid)) if s <= _aware(c.created_at) <= e]
    outcomes: dict[str, dict] = defaultdict(lambda: {"cases": 0, "recovered": 0, "unrecovered": 0, "open": 0, "recovered_minor": 0})
    for c in cases:
        k = c.failure_category or ("PENDING" if c.case_type == "PENDING_PAYMENT" else "UNKNOWN")
        o = outcomes[k]
        o["cases"] += 1
        if c.status == "RECOVERED":
            o["recovered"] += 1
            o["recovered_minor"] += c.recovered_amount_minor
        elif c.status == "CLOSED_UNRECOVERED":
            o["unrecovered"] += 1
        elif c.status in ("OPEN", "AWAITING_APPROVAL", "IN_PROGRESS"):
            o["open"] += 1
    outcome_rows = [{"category": k, **v, "rate": round(v["recovered"] / (v["recovered"] + v["unrecovered"]), 3) if (v["recovered"] + v["unrecovered"]) else None}
                    for k, v in sorted(outcomes.items(), key=lambda kv: -kv[1]["cases"])]
    abandoned = sum(1 for x in db.scalars(select(CheckoutSession).where(CheckoutSession.merchant_id == mid, CheckoutSession.status == "ABANDONED"))
                    if s <= _aware(x.created_at) <= e)
    result = {
        "window": {"start": s.isoformat(), "end": e.isoformat()},
        "totals": {"attempts": len(pays), "failed": len(failed), "failed_minor": sum(p.amount_minor for p in failed),
                   "failure_rate": round(len(failed) / len(pays), 4) if pays else None, "abandoned_sessions": abandoned,
                   "uncertain": sum(1 for p in failed if p.reconcile_required)},
        "categories": [{"category": k, "label": CATEGORY_INFO.get(k, {}).get("label", k), "group": GROUP_OF.get(k, "unknown"), "count": v,
                        "amount_minor": sum(p.amount_minor for p in failed if p._cat == k)} for k, v in cats.most_common()],
        "groups": [{"group": g, "count": groups.get(g, 0)} for g in GROUPS],
        "trend": trend_rows, "patterns": pattern_rows[:15], "repeat_customers": repeat_customers, "repeat_orders": repeat_orders,
        "outcomes": outcome_rows,
    }
    result["insights"] = insights(result)
    return result


def insights(r: dict) -> list[dict]:
    """Deterministic, evidence-backed observations. No numbers are invented: every figure comes from `r`."""
    out = []
    t = r["totals"]
    if not t["attempts"]:
        return [{"severity": "info", "title": "No payment activity in this period", "evidence": "0 payment attempts",
                 "recommendation": "Connect PayPal and run a test checkout to start monitoring.", "generated_by": "computed"}]
    if t["failed"] and r["categories"]:
        top = r["categories"][0]
        share = top["count"] / t["failed"]
        out.append({"severity": "high" if share >= 0.4 else "medium", "title": f"{top['label']} is your #1 failure cause",
                    "evidence": f"{top['count']} of {t['failed']} failed attempts ({share:.0%}), {money(top['amount_minor'])} affected",
                    "recommendation": {
                        "CARD_DECLINED": "Lead recovery with PayPal Pay Later or another funding source instead of retrying the same card.",
                        "INSUFFICIENT_FUNDS": "Offer PayPal Pay Later so eligible buyers can split the cost.",
                        "AUTHENTICATION_REQUIRED": "Send a fresh buyer-approved checkout link quickly — these buyers only need one more step.",
                        "CONFIGURATION_ISSUE": "Fix the PayPal account/currency configuration first; contacting buyers won't help.",
                        "TECHNICAL_ERROR": "Reconcile with PayPal before retrying; check your integration's timeouts.",
                    }.get(top["category"], "Review these cases in the Recovery Queue."), "generated_by": "computed", "filter": {"category": top["category"]}})
    cfg = next((c for c in r["categories"] if c["category"] == "CONFIGURATION_ISSUE"), None)
    if cfg:
        out.append({"severity": "high", "title": "Merchant-side PayPal configuration is blocking payments",
                    "evidence": f"{cfg['count']} attempt(s), {money(cfg['amount_minor'])}", "generated_by": "computed",
                    "recommendation": "Check currency settings and account status in your PayPal business account.", "filter": {"category": "CONFIGURATION_ISSUE"}})
    if r["repeat_customers"]:
        rc = r["repeat_customers"][0]
        out.append({"severity": "medium", "title": f"{len(r['repeat_customers'])} customer(s) failed more than once",
                    "evidence": f"e.g. {rc['customer']} with {rc['failures']} failed attempts; {r['repeat_orders']} order(s) failed repeatedly",
                    "recommendation": "For repeat failures, use the PayPal Invoice rail instead of another checkout attempt.", "generated_by": "computed"})
    rated = [o for o in r["outcomes"] if o["rate"] is not None and (o["recovered"] + o["unrecovered"]) >= 2]
    if len(rated) >= 2:
        best, worst = max(rated, key=lambda o: o["rate"]), min(rated, key=lambda o: o["rate"])
        if best["category"] != worst["category"]:
            out.append({"severity": "info", "title": "Recovery works best for " + CATEGORY_INFO.get(best["category"], {}).get("label", best["category"]).lower(),
                        "evidence": f"{best['recovered']}/{best['recovered'] + best['unrecovered']} recovered vs "
                                    f"{worst['recovered']}/{worst['recovered'] + worst['unrecovered']} for {CATEGORY_INFO.get(worst['category'], {}).get('label', worst['category']).lower()}",
                        "recommendation": "Prioritise high-probability categories first; review strategy for the weakest one.", "generated_by": "computed"})
    if t["uncertain"]:
        out.append({"severity": "high", "title": f"{t['uncertain']} payment(s) have an uncertain outcome",
                    "evidence": "Capture timed out or PayPal returned a server error", "generated_by": "computed",
                    "recommendation": "Run reconciliation (Payments → Reconcile with PayPal) before any retry."})
    if t["abandoned_sessions"]:
        out.append({"severity": "medium", "title": f"{t['abandoned_sessions']} checkout(s) abandoned", "evidence": "Checkout sessions inactive with no completed payment",
                    "recommendation": "Approve the queued reminders; abandoned checkouts are tracked separately from failed payments.", "generated_by": "computed"})
    return out[:6]


def ai_summary(db: Session, mid: str, r: dict) -> dict:
    from . import llm
    facts = {"totals": r["totals"], "top_categories": r["categories"][:5], "top_patterns": [{k: p[k] for k in ("code", "count", "category")} for p in r["patterns"][:5]],
             "outcomes": r["outcomes"][:6], "insights": [{k: i[k] for k in ("title", "evidence")} for i in r["insights"]]}
    try:
        out = llm.complete_json(
            "Summarise these payment-failure facts for a busy merchant in 3 short bullet points and one recommended next step. "
            "Use ONLY numbers present in the facts; amounts ending in _minor are cents. Do not speculate about causes not in the facts.\n"
            f"FACTS: {facts}\nReturn JSON {{\"bullets\": [str, str, str], \"next_step\": str}}",
            system="You are a precise payments analyst.", db=db, merchant_id=mid, purpose="insight_summary", max_tokens=500)
        return {"available": True, "bullets": [str(b)[:300] for b in (out.get("bullets") or [])][:4], "next_step": str(out.get("next_step", ""))[:300],
                "model": out.get("_model"), "generated_by": "llm"}
    except llm.LLMUnavailable as e:
        return {"available": False, "reason": str(e), "generated_by": None}
