"""Exercise the LLM code paths with a scripted model (the real Anthropic API is not called in tests)."""
from app.services import llm


def test_analysis_uses_llm_explanation_but_rules_decide(monkeypatch, client, demo):
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {"explanation": "PayPal asked the buyer to confirm the payment.", "root_cause": "Buyer step pending."})
    p = client.get("/api/payments?q=TXN-2048", headers=demo).json()["items"][0]
    a = client.post(f"/api/payments/{p['id']}/analyse", headers=demo).json()["analysis"]
    assert a["generated_by"] == "llm" and a["explanation"].startswith("PayPal asked")
    assert a["failure_category"] == "AUTHENTICATION_REQUIRED" and a["recommendation"]["action"] == "REQUEST_CUSTOMER_ACTION"


def test_copilot_tool_loop(monkeypatch, client, demo):
    calls = {"n": 0}

    def fake_chat(messages, system, tools=None, max_tokens=1200, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            assert tools and any(t["name"] == "get_recovery_cases" for t in tools)
            return {"content": [{"type": "text", "text": "Checking."},
                                {"type": "tool_use", "id": "tu_1", "name": "get_recovery_cases", "input": {"limit": 3}}]}
        tool_result = messages[-1]["content"][0]
        assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "tu_1" and "cases" in tool_result["content"]
        return {"content": [{"type": "text", "text": "Top case: Noah Williams."}]}

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post("/api/ai/chat", json={"message": "What needs attention?"}, headers=demo).json()
    assert r["generated_by"] == "llm" and r["answer"] == "Top case: Noah Williams." and r["tool_calls"][0]["tool"] == "get_recovery_cases"


def test_llm_failure_falls_back(monkeypatch, client, demo):
    def boom(*a, **k):
        raise llm.LLMUnavailable("down")
    monkeypatch.setattr(llm, "chat", boom)
    r = client.post("/api/ai/chat", json={"message": "How much revenue did I recover this week?"}, headers=demo).json()
    assert r["generated_by"] == "rules" and "recovered" in r["answer"]
