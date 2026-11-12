"""RecoverAI MCP server — run payment recovery from Claude Desktop, Cursor, or any MCP client.

It is a thin client of the RecoverAI REST API, so every Trust Firewall rule still applies: the assistant can analyse,
explain, draft and *request* approval, but it can never send a customer message, capture, refund or change an amount.

Env:
  RECOVERAI_API_URL   default http://localhost:8000
  RECOVERAI_EMAIL / RECOVERAI_PASSWORD   merchant login (omit both to use the demo workspace)
  RECOVERAI_APP_URL   dashboard URL for approval links, default http://localhost:5173

Run:  python server.py              (stdio, for Claude Desktop)
      python server.py --http 8765  (streamable HTTP at /mcp)
"""
import os
import sys

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

API = os.environ.get("RECOVERAI_API_URL", "http://localhost:8000").rstrip("/")
APP = os.environ.get("RECOVERAI_APP_URL", "http://localhost:5173").rstrip("/")
_token: str | None = None

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

mcp = MCPServer(
    name="recoverai",
    title="RecoverAI — PayPal revenue recovery",
    instructions=("Tools for a PayPal merchant's failed, pending and abandoned payments. Amounts ending in _minor are cents. "
                  "You may analyse, explain, forecast and draft. Customer messages are only sent after the merchant approves "
                  "in the RecoverAI dashboard — give them the approval link returned by request_recovery_approval."),
)


def _login() -> str:
    global _token
    if _token:
        return _token
    email, pw = os.environ.get("RECOVERAI_EMAIL"), os.environ.get("RECOVERAI_PASSWORD")
    r = httpx.post(f"{API}/api/auth/login", json={"email": email, "password": pw}, timeout=20) if email and pw \
        else httpx.post(f"{API}/api/auth/demo", timeout=20)
    r.raise_for_status()
    _token = r.json()["token"]
    return _token


def _call(method: str, path: str, body: dict | None = None) -> dict:
    global _token
    for attempt in (0, 1):
        r = httpx.request(method, f"{API}{path}", json=body, timeout=60, headers={"Authorization": f"Bearer {_login()}"})
        if r.status_code == 401 and attempt == 0:
            _token = None
            continue
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:300]
            return {"error": detail, "status": r.status_code}
        return r.json()
    return {"error": "authentication failed"}


@mcp.tool(annotations=READ)
def get_revenue_at_risk(days: int = 30) -> dict:
    """Revenue at risk, revenue recovered (PayPal-confirmed vs demo), success and recovery rates for the last N days."""
    o = _call("GET", f"/api/analytics/overview?days={max(0, min(days, 365))}")
    if "error" in o:
        return o
    keep = ("currency", "total_attempts", "successful", "failed", "pending", "abandoned_sessions", "open_cases", "revenue_at_risk_minor",
            "revenue_recovered_minor", "recovered_count", "recovered_by_source", "success_rate", "recovery_conversion_rate", "definitions")
    return {k: o.get(k) for k in keep}


@mcp.tool(annotations=READ)
def list_priority_cases(limit: int = 8) -> dict:
    """Open recovery cases ranked by expected recoverable value, with the reason for each rank."""
    items = _call("GET", "/api/recovery/cases?status=active&sort=priority").get("items", [])[: max(1, min(limit, 50))]
    return {"cases": [{"case_id": c["id"], "customer": c["customer"]["name"], "order": c["order_number"], "amount_minor": c["amount_minor"],
                       "category": c["failure_category"], "probability": c["recovery_probability"], "status": c["status"],
                       "awaiting_approval": c["awaiting_approval"], "why": c["priority_reason"]} for c in items]}


@mcp.tool()
def explain_payment(reference: str) -> dict:
    """Run RecoverAI's failure analysis on a payment (e.g. TXN-2048): category from PayPal's own codes, explanation,
    recovery probability with factors, recommended action and PayPal recovery rail."""
    found = _call("GET", f"/api/payments?q={reference.strip()}&limit=1").get("items", [])
    if not found:
        return {"error": f"No payment {reference}"}
    res = _call("POST", f"/api/payments/{found[0]['id']}/analyse")
    if "error" in res:
        return res
    a = res["analysis"]
    return {"reference": reference, "case_id": res["case_id"], "category": a["failure_category"], "explanation": a["explanation"],
            "root_cause": a["root_cause"], "probability": a["recovery_probability"], "factors": a["probability_factors"],
            "recommended_action": a["recommendation"]["action"], "reason": a["recommendation"]["reason"],
            "rail": a["recommendation"].get("rail"), "evidence": a["recommendation"].get("evidence")}


@mcp.tool(annotations=READ)
def get_agent_trace(case_id: str) -> dict:
    """The Trust Firewall trace for a case: each pipeline step and each guardrail check (pass / fail / hold)."""
    d = _call("GET", f"/api/recovery/cases/{case_id}")
    if "error" in d:
        return d
    run = (d.get("runs") or [None])[0]
    return {"case_status": d["case"]["status"], "latest_run": run}


@mcp.tool(annotations=READ)
def draft_recovery_message(case_id: str, tone: str = "friendly") -> dict:
    """Draft (never send) a personalised recovery email: professional, friendly or urgent."""
    tone = tone if tone in ("professional", "friendly", "urgent") else "friendly"
    return _call("POST", f"/api/recovery/{case_id}/preview-message", {"tone": tone})


@mcp.tool(annotations=READ)
def request_recovery_approval(case_id: str) -> dict:
    """Prepare the next recovery action for human approval. Returns the dashboard link where the merchant approves it.
    This tool never sends anything itself."""
    d = _call("GET", f"/api/recovery/cases/{case_id}")
    if "error" in d:
        return d
    proposed = [a for a in d["case"]["actions"] if a["status"] == "PROPOSED"]
    if not proposed:
        return {"result": "Nothing is waiting for approval on this case.", "case_status": d["case"]["status"]}
    return {"requires_human_approval": True, "action": proposed[0]["action_type"], "reason": proposed[0]["reason"],
            "approve_here": f"{APP}/recovery?case={case_id}"}


@mcp.tool(annotations=READ)
def revenue_forecast() -> dict:
    """Estimated recoverable revenue for three strategies, with confidence ranges. Estimates — not actual revenue."""
    r = _call("POST", "/api/simulator/run", {})
    if "error" in r:
        return r
    return {"scenarios": r["scenarios"], "basis": r["basis"], "disclaimer": r["disclaimer"]}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def run_failure_lab(scenario: str = "card_declined") -> dict:
    """Sandbox only: create a real PayPal Sandbox order and trigger a genuine PayPal decline via negative testing,
    so the agent can be observed reacting. Scenarios: card_declined, payer_action, high_value, refused, abandoned."""
    return _call("POST", "/api/lab/run", {"scenario": scenario})


if __name__ == "__main__":
    if "--http" in sys.argv:
        port = int(sys.argv[sys.argv.index("--http") + 1]) if len(sys.argv) > sys.argv.index("--http") + 1 else 8765
        mcp.run("streamable-http", port=port)
    else:
        mcp.run("stdio")
