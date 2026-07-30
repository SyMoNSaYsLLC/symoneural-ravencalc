"""Natural-language front end for RavenCalc.

The division of labour here is the whole point, and it runs one way only:

    the LLM parses and explains, SymPy computes.

A language model asked to integrate something will produce a confident,
plausible, and occasionally wrong answer. SymPy will not. So the model never
sees the job of computing -- it turns "what's the derivative of x squared sine x"
into a structured ComputeRequest, hands that to the existing engine, and is then
given the *exact* result to narrate. If the model is unreachable, the endpoint
degrades to an error rather than guessing.

Because SymPy is the whole calculator, RavenCalc runs perfectly well with no
LLM at all -- that is standalone mode (RAVENCALC_NO_AI=1, or run.sh
--standalone). The ask routes stay mounted in that mode and answer with the
reason the AI layer is off, which is more use than a 404. They keep returning
HTTP 200 with ok=false, as they always have for an unreachable model.

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
LLM_MODEL = os.environ.get("RAVENCALC_LLM_MODEL", "symon")
LLM_TIMEOUT = float(os.environ.get("RAVENCALC_LLM_TIMEOUT", "120"))
# Static key, if the engine is served with one (VLLM_API_KEY). Note the
# fallback: VLLM_API_KEY is exported from ~/.bashrc, so a service or a
# non-login shell will not have it and this ends up empty.
LLM_API_KEY = os.environ.get("RAVENCALC_LLM_API_KEY") or os.environ.get("VLLM_API_KEY", "")

# Standalone mode: RavenCalc without the language model. SymPy is the whole
# calculator, so /ui/compute and /api/compute are unaffected -- only the
# natural-language front end needs the LLM. Set by run.sh --standalone/--no-ai.
# The ask routes stay mounted and say the layer is off rather than 404ing.
AI_DISABLED = bool(os.environ.get("RAVENCALC_NO_AI", "").strip())

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

    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    req = urllib.request.Request(
        f"{LLM_URL}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    # No proxy: these are loopback calls and an ambient HTTP_PROXY would send
    # them somewhere surprising.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=LLM_TIMEOUT) as r:
        payload = json.load(r)
    return (payload["choices"][0]["message"].get("content") or "").strip()


_MATH_STILL_WORKS = (
    "RavenCalc's /ui/compute and /api/compute still work -- SymPy does the "
    "maths and only natural-language input needs the model."
)


def _llm_probe() -> tuple[bool, str]:
    """Is the LLM both reachable AND willing to talk to us?

    Those fail differently and this used to conflate them. The old check hit
    /health, which is unauthenticated on our engine: it returned 200 whenever
    the process was alive, _llm_up() said True, and the real authenticated
    call then came back 401 as a surprise. So probe a guarded route (/v1/models
    is the cheapest) with the key we would actually send, and report which of
    the two went wrong.

    Returns (usable, detail). detail is "" when usable.
    """
    if AI_DISABLED:
        return False, f"AI unavailable: standalone mode (RAVENCALC_NO_AI). {_MATH_STILL_WORKS}"

    headers = {}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    req = urllib.request.Request(f"{LLM_URL}/v1/models", headers=headers, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    try:
        with opener.open(req, timeout=3) as r:
            if r.status == 200:
                return True, ""
            return False, f"AI unavailable: {LLM_URL} answered HTTP {r.status}. {_MATH_STILL_WORKS}"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            # The distinction the owner actually needs: no key at all is a
            # config gap (VLLM_API_KEY is only in ~/.bashrc, so anything not
            # started from a login shell has none), a rejected key is a wrong
            # value.
            if not LLM_API_KEY:
                return False, (
                    "AI unavailable: no API key. The LLM at "
                    f"{LLM_URL} requires one -- set RAVENCALC_LLM_API_KEY in .env, "
                    "or launch from a login shell so VLLM_API_KEY is exported. "
                    f"{_MATH_STILL_WORKS}"
                )
            return False, (
                f"AI unavailable: the LLM at {LLM_URL} rejected our API key "
                f"(HTTP {exc.code}). Check RAVENCALC_LLM_API_KEY against the key the "
                f"engine was started with. {_MATH_STILL_WORKS}"
            )
        return False, f"AI unavailable: {LLM_URL} answered HTTP {exc.code}. {_MATH_STILL_WORKS}"
    except Exception as exc:
        return False, (
            f"AI unavailable: no LLM reachable at {LLM_URL} ({exc}). "
            "Start it with: cd /home/google/AI_Local_NVIDIA && make to-llm. "
            f"{_MATH_STILL_WORKS}"
        )


def _llm_up() -> bool:
    """Kept for /ai/health's llm_up field, which predates _llm_probe()."""
    return _llm_probe()[0]


def _ask(payload: AskRequest) -> dict:
    usable, detail = _llm_probe()
    if not usable:
        return {"ok": False, "ai_enabled": not AI_DISABLED, "detail": detail}

    # 1. Parse. Structured output keeps this inside the engine's vocabulary.
    try:
        raw = _chat(_PARSE_SYSTEM, payload.question, schema=_PARSE_SCHEMA, max_tokens=400)
        parsed = json.loads(raw)
    except urllib.error.HTTPError as exc:
        # HTTPError is a URLError subclass, so this has to come first or an
        # auth failure gets reported as "unreachable" -- which is the same
        # misleading answer _llm_probe() exists to avoid. The probe passed a
        # moment ago, so landing here means the key or the engine changed.
        return {"ok": False, "detail": f"LLM refused the request: HTTP {exc.code}. {_MATH_STILL_WORKS}"}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"ok": False, "detail": f"LLM unreachable: {exc}. {_MATH_STILL_WORKS}"}
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
    # ok/llm_url/llm_up/model are the original fields and keep their meaning;
    # ai_enabled and detail are additions, so existing callers are unaffected.
    usable, detail = _llm_probe()
    return {
        "ok": True,
        "llm_url": LLM_URL,
        "llm_up": usable,
        "model": LLM_MODEL,
        "ai_enabled": not AI_DISABLED,
        "detail": detail or None,
    }
