from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List, Tuple

import sympy as sp


@dataclass
class EngineResult:
    text: str
    latex: Optional[str] = None
    steps: Optional[List[str]] = None


def _symbols(vars: Optional[List[str]]) -> dict:
    if not vars:
        return {}
    out = {}
    for v in vars:
        v = (v or "").strip()
        if not v:
            continue
        out[v] = sp.Symbol(v)
    return out


def _parse_expr(expr: str, locals_map: dict) -> sp.Expr:
    locals_map = dict(locals_map or {})
    # common constants
    locals_map.update({"pi": sp.pi, "e": sp.E})
    return sp.sympify(expr, locals=locals_map)


def _norm_wrt(wrt: Optional[str]) -> str:
    w = (wrt or "").strip()
    return w if w else "x"


def simplify(expr: str, variables: Optional[List[str]] = None) -> EngineResult:
    locals_map = _symbols(variables)
    e = _parse_expr(expr, locals_map)
    r = sp.simplify(e)
    return EngineResult(text=str(r), latex=sp.latex(r))


# ---------- LIGHTWEIGHT DERIVATIVE STEPS ----------
def _is_const(e: sp.Expr, x: sp.Symbol) -> bool:
    return (x not in e.free_symbols)


def _pretty(e: sp.Expr) -> str:
    # readable string for steps
    return str(e)


def _steps_diff(e: sp.Expr, x: sp.Symbol) -> Tuple[sp.Expr, List[str]]:
    """
    Best-effort, "lightweight" step generator.
    Returns (derivative_expr, steps_list).
    Falls back to sympy.diff when it can't decompose nicely.
    """
    steps: List[str] = []

    # constants
    if _is_const(e, x):
        steps.append(f"Constant rule: d/d{x}({_pretty(e)}) = 0")
        return sp.Integer(0), steps

    # variable itself
    if e == x:
        steps.append(f"Basic rule: d/d{x}({_pretty(x)}) = 1")
        return sp.Integer(1), steps

    # sum rule
    if isinstance(e, sp.Add):
        steps.append("Sum rule: d/dx(a + b + …) = a' + b' + …")
        deriv_parts = []
        for term in e.args:
            dt, st = _steps_diff(term, x)
            steps.extend(st)
            deriv_parts.append(dt)
        return sp.Add(*deriv_parts), steps

    # product rule (simple: treat as product of factors)
    if isinstance(e, sp.Mul):
        steps.append("Product rule: d/dx(u·v) = u'·v + u·v' (generalized across factors)")
        factors = list(e.args)
        terms = []
        for i, fi in enumerate(factors):
            dfi, st = _steps_diff(fi, x)
            steps.extend(st)
            others = factors[:i] + factors[i+1:]
            terms.append(sp.Mul(dfi, *others))
        return sp.Add(*terms), steps

    # power rule / chain rule for powers
    if isinstance(e, sp.Pow):
        base, exp = e.args

        # x^n
        if base == x and _is_const(exp, x):
            steps.append("Power rule: d/dx(x^n) = n·x^(n-1)")
            r = exp * (x ** (exp - 1))
            return r, steps

        # (g(x))^n with constant n
        if (not _is_const(base, x)) and _is_const(exp, x):
            steps.append("Chain rule (power): d/dx((g(x))^n) = n·(g(x))^(n-1)·g'(x)")
            dg, st = _steps_diff(base, x)
            steps.extend(st)
            r = exp * (base ** (exp - 1)) * dg
            return r, steps

        # general power f(x)^g(x) (fallback)
        steps.append("General power form f(x)^g(x) detected → using SymPy diff (fallback).")
        r = sp.diff(e, x)
        return r, steps

    # quotient rule: recognize u/v
    if e.is_rational_function(x):
        # This can still be complex; do a safe structural check:
        num, den = sp.fraction(e)
        if den != 1 and (x in den.free_symbols):
            steps.append("Quotient rule: d/dx(u/v) = (u'·v - u·v') / v^2")
            du, st1 = _steps_diff(num, x)
            dv, st2 = _steps_diff(den, x)
            steps.extend(st1)
            steps.extend(st2)
            r = (du * den - num * dv) / (den ** 2)
            return r, steps

    # common functions with chain rule
    if isinstance(e, sp.Function):
        # e = f(g(x))
        arg = e.args[0] if e.args else None
        if arg is not None and not _is_const(arg, x):
            fname = e.func.__name__
            steps.append(f"Chain rule: d/dx({fname}(g(x))) = {fname}'(g(x)) · g'(x)")
            dg, st = _steps_diff(arg, x)
            steps.extend(st)
            # use sympy for outer derivative, then multiply by g'
            outer = sp.diff(e.func(sp.Symbol("u")), sp.Symbol("u")).subs({sp.Symbol("u"): arg})
            r = outer * dg
            return r, steps

    # fallback
    steps.append("Using SymPy diff (fallback) for this structure.")
    r = sp.diff(e, x)
    return r, steps


def differentiate(expr: str, wrt: str, n: int = 1, variables: Optional[List[str]] = None) -> EngineResult:
    wrt = _norm_wrt(wrt)
    locals_map = _symbols(variables or ([wrt] if wrt else []))
    e = _parse_expr(expr, locals_map)
    x = locals_map.get(wrt) if wrt in locals_map else sp.Symbol(wrt)
    r = sp.diff(e, x, int(n or 1))
    return EngineResult(text=str(r), latex=sp.latex(r))


def differentiate_steps(expr: str, wrt: str, n: int = 1, variables: Optional[List[str]] = None) -> EngineResult:
    """
    Derivative with lightweight steps. For n>1, steps are best-effort:
    we show "take derivative repeatedly" then compute.
    """
    wrt = _norm_wrt(wrt)
    locals_map = _symbols(variables or ([wrt] if wrt else []))
    e = _parse_expr(expr, locals_map)
    x = locals_map.get(wrt) if wrt in locals_map else sp.Symbol(wrt)

    order = int(n or 1)
    steps: List[str] = []
    cur = e

    if order > 1:
        steps.append(f"Computing {order}ᵗʰ derivative: apply d/d{wrt} repeatedly.")

    for k in range(order):
        dcur, st = _steps_diff(cur, x)
        if order > 1:
            steps.append(f"Step {k+1}: Differentiate current expression:")
        steps.extend(st)
        cur = sp.simplify(dcur)

    return EngineResult(text=str(cur), latex=sp.latex(cur), steps=steps)


def integrate(expr: str, wrt: str, a: Optional[str] = None, b: Optional[str] = None, variables: Optional[List[str]] = None) -> EngineResult:
    wrt = _norm_wrt(wrt)
    locals_map = _symbols(variables or ([wrt] if wrt else []))
    e = _parse_expr(expr, locals_map)
    x = locals_map.get(wrt) if wrt in locals_map else sp.Symbol(wrt)
    if a is not None and b is not None and str(a).strip() and str(b).strip():
        a_e = _parse_expr(str(a), locals_map)
        b_e = _parse_expr(str(b), locals_map)
        r = sp.integrate(e, (x, a_e, b_e))
    else:
        r = sp.integrate(e, x)
    return EngineResult(text=str(r), latex=sp.latex(r))


def _parse_limit_student_format(raw: str) -> Optional[Tuple[str, str, str, str]]:
    """
    Accepts: "f(x), x->0", "f(x), x->0+", "f(x), x->0-", and returns:
      (expr, wrt, to, dir)
    dir is "", "+", or "-"
    """
    if not raw:
        return None

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) < 2:
        return None

    expr_part = parts[0]
    spec = ",".join(parts[1:]).strip()

    spec = spec.replace("→", "->").replace("⇒", "->")

    import re
    m = re.match(r"^([a-zA-Z]\w*)\s*-\>\s*([^\s]+)\s*([+-])?$", spec)
    if not m:
        return None

    wrt = m.group(1)
    to = m.group(2)
    d = m.group(3) or ""
    return (expr_part, wrt, to, d)


def limit(
    expr: str,
    wrt: str,
    limit_to: str,
    dir: Optional[str] = None,
    variables: Optional[List[str]] = None
) -> EngineResult:
    parsed = _parse_limit_student_format(expr)
    if parsed:
        expr, wrt, limit_to, parsed_dir = parsed
        if (dir or "").strip() == "":
            dir = parsed_dir

    wrt = _norm_wrt(wrt)

    locals_map = _symbols(variables or ([wrt] if wrt else []))
    e = _parse_expr(expr, locals_map)
    x = locals_map.get(wrt) if wrt in locals_map else sp.Symbol(wrt)

    lt_raw = str(limit_to).strip()
    if lt_raw.endswith("+") or lt_raw.endswith("-"):
        if (dir or "").strip() == "":
            dir = lt_raw[-1]
        lt_raw = lt_raw[:-1].strip()

    t = _parse_expr(lt_raw, locals_map)

    d = (dir or "").strip()
    if d not in ("", "+", "-"):
        d = ""

    if d in ("+", "-"):
        r = sp.limit(e, x, t, dir=d)
    else:
        r = sp.limit(e, x, t)

    return EngineResult(text=str(r), latex=sp.latex(r))


def solve(expr: str, wrt: str, variables: Optional[List[str]] = None) -> EngineResult:
    wrt = _norm_wrt(wrt)
    locals_map = _symbols(variables or ([wrt] if wrt else []))
    if "=" in expr:
        left, right = expr.split("=", 1)
        equation = sp.Eq(_parse_expr(left, locals_map), _parse_expr(right, locals_map))
    else:
        equation = sp.Eq(_parse_expr(expr, locals_map), 0)
    x = locals_map.get(wrt) if wrt in locals_map else sp.Symbol(wrt)
    r = sp.solve(equation, x)
    text = ", ".join([str(v) for v in r]) if isinstance(r, (list, tuple)) else str(r)
    return EngineResult(text=text, latex=sp.latex(r))