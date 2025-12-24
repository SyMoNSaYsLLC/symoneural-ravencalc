from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List

import sympy as sp

@dataclass
class EngineResult:
    text: str
    latex: Optional[str] = None

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
    locals_map.update({"pi": sp.pi, "e": sp.E})
    return sp.sympify(expr, locals=locals_map)

def simplify(expr: str, variables: Optional[List[str]] = None) -> EngineResult:
    locals_map = _symbols(variables)
    e = _parse_expr(expr, locals_map)
    r = sp.simplify(e)
    return EngineResult(text=str(r), latex=sp.latex(r))

def differentiate(expr: str, wrt: str, n: int = 1, variables: Optional[List[str]] = None) -> EngineResult:
    locals_map = _symbols(variables or ([wrt] if wrt else []))
    e = _parse_expr(expr, locals_map)
    x = locals_map.get(wrt) if wrt in locals_map else sp.Symbol(wrt)
    r = sp.diff(e, x, int(n or 1))
    return EngineResult(text=str(r), latex=sp.latex(r))

def integrate(expr: str, wrt: str, a: Optional[str] = None, b: Optional[str] = None, variables: Optional[List[str]] = None) -> EngineResult:
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

def limit(expr: str, wrt: str, limit_to: str, variables: Optional[List[str]] = None) -> EngineResult:
    locals_map = _symbols(variables or ([wrt] if wrt else []))
    e = _parse_expr(expr, locals_map)
    x = locals_map.get(wrt) if wrt in locals_map else sp.Symbol(wrt)
    t = _parse_expr(str(limit_to), locals_map)
    r = sp.limit(e, x, t)
    return EngineResult(text=str(r), latex=sp.latex(r))

def solve(expr: str, wrt: str, variables: Optional[List[str]] = None) -> EngineResult:
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
