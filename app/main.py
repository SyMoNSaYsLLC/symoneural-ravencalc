from __future__ import annotations

from pathlib import Path
from fastapi import FastAPI, Request
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

def _compute(payload: ComputeRequest) -> ComputeResponse:
    op = (payload.operation or "").strip().lower()
    expr = (payload.expression or "").strip()
    if len(expr) > settings.max_expr_len:
        raise ValueError(f"Expression too long (max {settings.max_expr_len})")

    vars_ = payload.variables or []
    wrt = (payload.wrt or "x").strip() or "x"
    n = int(payload.n or 1)

    if op == "simplify":
        r = eng.simplify(expr, vars_)
    elif op in ("differentiate", "derivative", "diff"):
        r = eng.differentiate(expr, wrt=wrt, n=n, variables=vars_)
        op = "differentiate"
    elif op in ("integrate", "integral"):
        r = eng.integrate(expr, wrt=wrt, a=payload.a, b=payload.b, variables=vars_)
        op = "integrate"
    elif op == "limit":
        if payload.limit_to is None or str(payload.limit_to).strip() == "":
            raise ValueError("limit_to is required for limit operation")
        r = eng.limit(expr, wrt=wrt, limit_to=str(payload.limit_to), variables=vars_)
    elif op == "solve":
        r = eng.solve(expr, wrt=wrt, variables=vars_)
    else:
        raise ValueError("Unknown operation")

    return ComputeResponse(ok=True, operation=op, result=r.text, latex=r.latex)

@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))

# Raven UI endpoint (no key required)
@app.post("/ui/compute", response_model=ComputeResponse)
async def ui_compute(req: Request, payload: ComputeRequest):
    rl = _rate_limit_or_429(req)
    if rl:
        return rl
    try:
        return _compute(payload)
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "operation": payload.operation, "result": "", "detail": str(e)})

# Protected endpoint (heartbeat.php style)
@app.post("/api/compute", response_model=ComputeResponse)
async def api_compute(req: Request, payload: ComputeRequest, _=require_api_key):
    rl = _rate_limit_or_429(req)
    if rl:
        return rl
    try:
        return _compute(payload)
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "operation": payload.operation, "result": "", "detail": str(e)})
