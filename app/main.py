from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import settings
from .security import require_api_key
from .ratelimit import RateLimiter
from .schemas import ComputeRequest, ComputeResponse
from . import sympy_engine as eng

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"

limiter = RateLimiter(settings.rate_per_min)
app = FastAPI(title="SyMoNeuRaL • RavenCalc", version="1.0")


@app.get("/health")
def health():
    return {"ok": True, "service": "ravencalc", "rate_per_min": settings.rate_per_min}


def _client_key(req: Request) -> str:
    return (req.client.host if req.client else "unknown")


def _rate_limit_or_429(req: Request):
    if not limiter.allow(_client_key(req)):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    return None


# Accept: "sin(x)/x, x->0" or "(x^2-1)/(x-1), x->1" or "..., x->0+" / "..., x->0-"
_LIMIT_RE = re.compile(
    r"^\s*(?P<f>.+?)\s*,\s*(?P<wrt>[a-zA-Z]\w*)\s*-\>\s*(?P<to>[^,]+?)\s*(?P<dir>[+-])?\s*$"
)


def _normalize_expr_user_input(s: str) -> str:
    # Treat standalone X as x (common student habit)
    return re.sub(r"\bX\b", "x", (s or ""))


def _parse_limit_from_expression(expr: str) -> Optional[Tuple[str, str, str, str]]:
    """
    Returns (f, wrt, to, dir) if expression matches "f(x), x->0" or "f(x), x->0+/-"
    dir is "" or "+" or "-"
    """
    m = _LIMIT_RE.match(expr or "")
    if not m:
        return None
    f = m.group("f").strip()
    wrt = m.group("wrt").strip()
    to = m.group("to").strip()
    d = (m.group("dir") or "").strip()
    return (f, wrt, to, d)


def _compute(payload: ComputeRequest) -> ComputeResponse:
    op = (payload.operation or "").strip().lower()

    expr = _normalize_expr_user_input((payload.expression or "").strip())
    if len(expr) > settings.max_expr_len:
        raise ValueError(f"Expression too long (max {settings.max_expr_len})")

    vars_ = payload.variables or []
    wrt = _normalize_expr_user_input((payload.wrt or "x").strip() or "x")
    n = int(payload.n or 1)

    # NEW: lightweight derivative steps (best-effort)
    show_steps = bool(getattr(payload, "show_steps", False))

    if op == "simplify":
        r = eng.simplify(expr, vars_)

    elif op in ("differentiate", "derivative", "diff"):
        if show_steps and hasattr(eng, "differentiate_steps"):
            r = eng.differentiate_steps(expr, wrt=wrt, n=n, variables=vars_)
        else:
            r = eng.differentiate(expr, wrt=wrt, n=n, variables=vars_)
        op = "differentiate"

    elif op in ("integrate", "integral"):
        r = eng.integrate(expr, wrt=wrt, a=payload.a, b=payload.b, variables=vars_)
        op = "integrate"

    elif op == "limit":
        # Allow limit_to OR embedded "f(x), x->0" (and optional +/- direction)
        limit_to = payload.limit_to
        limit_dir = (getattr(payload, "limit_dir", None) or getattr(payload, "dir", None) or "").strip()

        if limit_to is None or str(limit_to).strip() == "":
            parsed = _parse_limit_from_expression(expr)
            if parsed:
                expr, wrt, limit_to, limit_dir = parsed
            else:
                raise ValueError(
                    "limit_to is required (or use 'f(x), x->0' / 'f(x), x->0+' / 'f(x), x->0-')"
                )

        r = eng.limit(expr, wrt=wrt, limit_to=str(limit_to), dir=limit_dir, variables=vars_)

    elif op == "solve":
        r = eng.solve(expr, wrt=wrt, variables=vars_)

    else:
        raise ValueError("Unknown operation")

    # NEW: include steps if engine provided them
    steps = getattr(r, "steps", None)

    return ComputeResponse(ok=True, operation=op, result=r.text, latex=r.latex, steps=steps)


@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


@app.post("/ui/compute", response_model=ComputeResponse)
async def ui_compute(req: Request, payload: ComputeRequest):
    rl = _rate_limit_or_429(req)
    if rl:
        return rl
    try:
        return _compute(payload)
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "operation": payload.operation, "result": "", "detail": str(e)},
        )


@app.post("/api/compute", response_model=ComputeResponse)
async def api_compute(req: Request, payload: ComputeRequest, _=Depends(require_api_key)):
    rl = _rate_limit_or_429(req)
    if rl:
        return rl
    try:
        return _compute(payload)
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "operation": payload.operation, "result": "", "detail": str(e)},
        )