"""Natural-language front end for RavenCalc.

The division of labour here is the whole point, and it runs one way only:

    the LLM parses and explains, SymPy computes.

A language model asked to integrate something will produce a confident,
plausible, and occasionally wrong answer. SymPy will not. So the model never
sees the job of computing -- it turns "what's the derivative of x squared sine x"
into a structured ComputeRequest, hands that to the existing engine, and is then
given the *exact* result to narrate. If the model is unreachable, the endpoint
degrades to an error rather than guessing.

This module adds routes to the existing app rather than modifying it. main.py,
sympy_engine.py, schemas.py, config.py, security.py and ratelimit.py are the
byte-for-byte Christmas 2025 backend and stay that way.

Uses urllib from the stdlib rather than httpx so requirements.txt is unchanged.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .main import app, _compute, _rate_limit_or_429
from .schemas import ComputeRequest
from .security import require_api_key

LLM_URL = os.environ.get("RAVENCALC_LLM_URL", "http://localhost:9001")
LLM_MODEL = os.environ.get("RAVENCALC_LLM_MODEL", "syosml-assistant")
LLM_TIMEOUT = float(os.environ.get("RAVENCALC_LLM_TIMEOUT", "120"))

# Constrains the model to the exact vocabulary sympy_engine already accepts.
# Without this it happily invents operations like "factor" or "expand".
_PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["simplify", "differentiate", "integrate", "limit", "solve"],
        },
        "expression": {"type": "string"},
        "wrt": {"type": "string"},
        "n": {"type": "integer"},
        "a": {"type": "string"},
        "b": {"type": "string"},
        "limit_to": {"type": "string"},
        "limit_dir": {"type": "string", "enum": ["+", "-", ""]},
    },
    "required": ["operation", "expression"],
    "additionalProperties": False,
}

_PARSE_SYSTEM = """You convert a natural-language calculus question into JSON for a SymPy backend.

Rules:
- Write `expression` in SymPy syntax: ** for powers, explicit * for multiplication,
  sin/cos/tan/exp/log/sqrt, pi and E as constants. Never use unicode math symbols.
- `operation` must be exactly one of: simplify, differentiate, integrate, limit, solve.
- differentiate: set `wrt` (default "x") and `n` for higher orders.
- integrate: set `wrt`; for a definite integral also set `a` and `b`.
- limit: set `wrt`, `limit_to` (e.g. "0", "oo", "-oo", "pi"), and `limit_dir`
  ("+" or "-") only if the question asks for a one-sided limit.
- Do NOT solve anything. Do NOT put an answer anywhere in the JSON.
  You are only transcribing the question."""

_EXPLAIN_SYSTEM = """You explain a calculus result that has ALREADY been computed exactly by SymPy.

The result you are given is correct and authoritative. Never contradict it, never
recompute it, and never present a different answer. Explain the method that leads
to it in 2-4 sentences, in plain language, as if to a student. No LaTeX."""


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    show_steps: bool = False
    explain: bool = True


def _chat(system: str, user: str, schema: Optional[dict] = None,
          max_tokens: int = 700) -> str:
    """One non-streaming chat turn. Thinking is off: parsing is transcription,
    and a reasoning block here just burns tokens and latency."""
    body: dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "compute_request", "schema": schema},
        }

    req = urllib.request.Request(
        f"{LLM_URL}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # No proxy: these are loopback calls and an ambient HTTP_PROXY would send
    # them somewhere surprising.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=LLM_TIMEOUT) as r:
        payload = json.load(r)
    return (payload["choices"][0]["message"].get("content") or "").strip()


def _llm_up() -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{LLM_URL}/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _ask(payload: AskRequest) -> dict:
    if not _llm_up():
        return {
            "ok": False,
            "detail": (
                f"No LLM at {LLM_URL}. RavenCalc's /ui/compute still works "
                "without it -- only natural-language input needs the model. "
                "Start it with: cd /home/google/AI_Local_NVIDIA && make to-llm"
            ),
        }

    # 1. Parse. Structured output keeps this inside the engine's vocabulary.
    try:
        raw = _chat(_PARSE_SYSTEM, payload.question, schema=_PARSE_SCHEMA, max_tokens=400)
        parsed = json.loads(raw)
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"ok": False, "detail": f"LLM unreachable: {exc}"}
    except json.JSONDecodeError:
        return {"ok": False, "detail": f"Could not parse the question into a request: {raw[:200]!r}"}

    # Drop empty optionals so the engine's own defaults apply.
    fields = {k: v for k, v in parsed.items() if v not in (None, "", [])}
    fields["show_steps"] = payload.show_steps

    try:
        creq = ComputeRequest(**fields)
    except Exception as exc:
        return {"ok": False, "detail": f"Model produced an invalid request ({exc}): {parsed}"}

    # 2. Compute -- the untouched Christmas backend, unchanged and authoritative.
    cres = _compute(creq)
    out = {
        "ok": cres.ok,
        "question": payload.question,
        "interpreted": fields,
        "operation": cres.operation,
        "result": cres.result,
        "latex": cres.latex,
        "steps": cres.steps,
        "detail": cres.detail,
    }
    if not cres.ok:
        return out

    # 3. Explain the exact result. Failure here is cosmetic, so it never
    #    invalidates a correct computation.
    if payload.explain:
        try:
            out["explanation"] = _chat(
                _EXPLAIN_SYSTEM,
                f"Question: {payload.question}\n"
                f"Operation: {cres.operation}\n"
                f"Exact result from SymPy: {cres.result}",
                max_tokens=500,
            )
        except Exception as exc:
            out["explanation"] = None
            out["explanation_error"] = str(exc)

    return out


@app.post("/ui/ask")
async def ui_ask(req: Request, payload: AskRequest):
    """Same-origin, no API key -- mirrors /ui/compute."""
    limited = _rate_limit_or_429(req)
    if limited is not None:
        return limited
    return JSONResponse(_ask(payload))


@app.post("/api/ask", dependencies=[Depends(require_api_key)])
async def api_ask(req: Request, payload: AskRequest):
    """Key-guarded -- mirrors /api/compute."""
    limited = _rate_limit_or_429(req)
    if limited is not None:
        return limited
    return JSONResponse(_ask(payload))


@app.get("/ai/health")
def ai_health():
    return {"ok": True, "llm_url": LLM_URL, "llm_up": _llm_up(), "model": LLM_MODEL}
