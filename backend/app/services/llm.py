
"""Provider-independent AI layer.

Supports Anthropic (Messages API, native tool use), OpenAI, and Google Gemini — Gemini through Google's
OpenAI-compatible endpoint (https://generativelanguage.googleapis.com/v1beta/openai), which has a free tier.
Callers always speak one format (Anthropic-style content blocks); this module translates. Every call is time-limited,
retried once on 429/5xx, and recorded in `ai_usage` (no prompts, outputs or keys are stored).
If no provider is configured or the call fails, LLMUnavailable is raised and callers show a transparent fallback.
"""
import json
import logging
import re
import time

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings

log = logging.getLogger("recoverai.llm")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class LLMUnavailable(Exception):
    pass


def _post(url: str, headers: dict, body: dict) -> httpx.Response:
    timeout = get_settings().ai_timeout_seconds

    for attempt in (0, 1):
        try:
            r = httpx.post(
                url,
                json=body,
                headers=headers,
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            if attempt == 0:
                time.sleep(1)
                continue
            raise LLMUnavailable(f"network: {type(e).__name__}")

        if r.status_code in (429, 500, 502, 503, 529) and attempt == 0:
            time.sleep(1.5)
            continue

        return r

    raise LLMUnavailable("unreachable")


def _record(db, mid, cfg, purpose, ok, started, usage=None, error=None):
    if db is None:
        return

    from ..models import AIUsage

    try:
        db.add(
            AIUsage(
                merchant_id=mid,
                provider=cfg["provider"],
                model=cfg["model"][:80],
                purpose=purpose[:40],
                success=ok,
                latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=(usage or {}).get("in"),
                output_tokens=(usage or {}).get("out"),
                error=(error or None) and str(error)[:300],
            )
        )
        db.flush()
    except Exception:
        log.exception("could not record AI usage")


# ---------------------------------------------------------------- Anthropic


def _anthropic(cfg, messages, system, tools, max_tokens):
    body = {
        "model": cfg["model"],
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }

    if tools:
        body["tools"] = tools

    r = _post(
        ANTHROPIC_URL,
        {
            "x-api-key": cfg["api_key"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        body,
    )

    if r.status_code >= 400:
        raise LLMUnavailable(
            f"Anthropic HTTP {r.status_code}: {_err(r)}"
        )

    data = r.json()
    u = data.get("usage") or {}

    return data, {
        "in": u.get("input_tokens"),
        "out": u.get("output_tokens"),
    }


# ---------------------------------------------------------------- OpenAI-compatible


def _to_openai_messages(system, messages):
    out = [
        {
            "role": "system",
            "content": system,
        }
    ]

    for m in messages:
        c = m["content"]

        if isinstance(c, str):
            out.append({
                "role": m["role"],
                "content": c,
            })
            continue

        if m["role"] == "assistant":
            text = "".join(
                b.get("text", "")
                for b in c
                if b.get("type") == "text"
            )

            calls = []

            for b in c:
                if b.get("type") != "tool_use":
                    continue

                call = {
                    "id": b["id"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        "arguments": json.dumps(b.get("input") or {}),
                    },
                }

                if b.get("_extra"):
                    # Gemini thought signatures must be echoed unchanged.
                    call["extra_content"] = b["_extra"]

                calls.append(call)

            msg = {
                "role": "assistant",
                "content": text or None,
            }

            if calls:
                msg["tool_calls"] = calls

            out.append(msg)

        else:
            for b in c:
                if b.get("type") == "tool_result":
                    out.append({
                        "role": "tool",
                        "tool_call_id": b["tool_use_id"],
                        "content": b.get("content") or "",
                    })

                elif b.get("type") == "text":
                    out.append({
                        "role": "user",
                        "content": b["text"],
                    })

    return out


PROVIDER_NAME = {
    "openai": "OpenAI",
    "gemini": "Gemini",
    "anthropic": "Anthropic",
}


def _openai(cfg, messages, system, tools, max_tokens):
    """
    OpenAI Chat Completions format — used for OpenAI and
    for Gemini's OpenAI-compatible endpoint.
    """
    gemini = cfg["provider"] == "gemini"

    body = {
        "model": cfg["model"],
        "messages": _to_openai_messages(system, messages),
    }

    if gemini:
        # Gemini counts internal thinking against the output budget.
        body["max_tokens"] = max_tokens + 2048
        body["reasoning_effort"] = "low"
    else:
        body["max_completion_tokens"] = max_tokens

    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": (
                        t.get("input_schema")
                        or {
                            "type": "object",
                            "properties": {},
                        }
                    ),
                },
            }
            for t in tools
        ]

    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    url = f"{cfg['base_url']}/chat/completions"

    r = _post(url, headers, body)

    if r.status_code == 400 and "max_completion_tokens" in r.text:
        # Older compatible servers.
        body["max_tokens"] = body.pop("max_completion_tokens")
        r = _post(url, headers, body)

    if (
        r.status_code == 400
        and "reasoning" in r.text.lower()
        and "reasoning_effort" in body
    ):
        # Retry models that do not support reasoning_effort.
        body.pop("reasoning_effort")
        r = _post(url, headers, body)

    name = PROVIDER_NAME.get(
        cfg["provider"],
        cfg["provider"],
    )

    if r.status_code == 429:
        hint = (
            " (free-tier limit reached; wait a minute or check your "
            "quota in Google AI Studio)"
            if gemini
            else ""
        )

        raise LLMUnavailable(
            f"{name} rate limit{hint}: {_err(r)}"
        )

    if r.status_code >= 400:
        raise LLMUnavailable(
            f"{name} HTTP {r.status_code}: {_err(r)}"
        )

    data = r.json()

    if isinstance(data, list):
        # Some Gemini responses arrive wrapped in a list.
        data = data[0] if data else {}

    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}

    blocks = []

    if msg.get("content"):
        blocks.append({
            "type": "text",
            "text": msg["content"],
        })

    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(
                tc["function"].get("arguments") or "{}"
            )
        except json.JSONDecodeError:
            args = {}

        block = {
            "type": "tool_use",
            "id": tc.get("id") or f"call_{len(blocks)}",
            "name": tc["function"]["name"],
            "input": args,
        }

        if tc.get("extra_content"):
            block["_extra"] = tc["extra_content"]

        blocks.append(block)

    if not blocks:
        raise LLMUnavailable(
            f"{name} returned an empty answer "
            f"(finish reason: {choice.get('finish_reason')})"
        )

    u = data.get("usage") or {}

    return (
        {"content": blocks},
        {
            "in": u.get("prompt_tokens"),
            "out": u.get("completion_tokens"),
        },
    )


def _err(r: httpx.Response) -> str:
    try:
        j = r.json()
        e = j.get("error")
        return (
            e.get("message")
            if isinstance(e, dict)
            else str(e or j)
        )[:200]
    except ValueError:
        return r.text[:200]


# ---------------------------------------------------------------- public API


def chat(
    messages: list[dict],
    *,
    system: str,
    tools: list[dict] | None = None,
    max_tokens: int = 1200,
    db: Session | None = None,
    merchant_id: str | None = None,
    purpose: str = "chat",
) -> dict:
    from .providers import ai_config

    cfg = ai_config(db, merchant_id)

    if not cfg:
        raise LLMUnavailable("No AI provider configured")

    started = time.monotonic()

    try:
        fn = (
            _anthropic
            if cfg["provider"] == "anthropic"
            else _openai
        )

        data, usage = fn(
            cfg,
            messages,
            system,
            tools,
            max_tokens,
        )

    except LLMUnavailable as e:
        log.warning(
            "AI call failed (%s/%s): %s",
            cfg["provider"],
            purpose,
            e,
        )

        _record(
            db,
            merchant_id,
            cfg,
            purpose,
            False,
            started,
            error=e,
        )

        raise

    _record(
        db,
        merchant_id,
        cfg,
        purpose,
        True,
        started,
        usage,
    )

    data["_model"] = f"{cfg['provider']}:{cfg['model']}"

    return data


def text_of(resp: dict) -> str:
    return "".join(
        b.get("text", "")
        for b in resp.get("content", [])
        if b.get("type") == "text"
    )


def complete_json(
    prompt: str,
    *,
    system: str,
    max_tokens: int = 800,
    db: Session | None = None,
    merchant_id: str | None = None,
    purpose: str = "json",
) -> dict:
    """Ask for a JSON object; raises LLMUnavailable on failure."""

    resp = chat(
        [{"role": "user", "content": prompt}],
        system=system + "\nRespond with a single JSON object only.",
        max_tokens=max_tokens,
        db=db,
        merchant_id=merchant_id,
        purpose=purpose,
    )

    m = re.search(
        r"\{.*\}",
        text_of(resp),
        re.S,
    )

    if not m:
        raise LLMUnavailable("No JSON in AI response")

    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMUnavailable(f"Bad JSON: {e}")

    if not isinstance(out, dict):
        raise LLMUnavailable("AI returned non-object JSON")

    out["_model"] = resp.get("_model")

    return out


def model_label(
    db: Session | None,
    merchant_id: str | None,
) -> str | None:
    from .providers import ai_config

    cfg = ai_config(db, merchant_id)

    return (
        f"{cfg['provider']}:{cfg['model']}"
        if cfg
        else None
    )
