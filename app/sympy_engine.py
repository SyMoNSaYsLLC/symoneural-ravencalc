from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional, List, Tuple

import sympy as sp

# An "=" that is not part of ==, >=, <= or !=.
_PLAIN_EQUALS = re.compile(r"(?<![<>!=])=(?!=)")

from .config import settings
from .safe_parse import validate_expression


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
    """The ONE place user text becomes a SymPy object.

    Every engine entry point routes through here, which is why the allowlist
    gate lives here and not in the HTTP layer: `/ui/compute` takes no
    credential, and there is more than one caller. sympify() evals its input
    (SymPy's own docstring says so), so an expression that has not passed
    validate_expression() must never reach it. See app/safe_parse.py for why
    this is an allowlist rather than a denylist.
    """
    validate_expression(expr, max_len=settings.max_expr_len)
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


# ---------- LIGHTWEIGHT INTEGRAL STEPS ----------
def _steps_int(e: sp.Expr, x: sp.Symbol) -> Tuple[sp.Expr, List[str]]:
    """
    Best-effort step generator for antiderivatives, mirroring _steps_diff.
    Returns (antiderivative_expr, steps_list); falls back to sympy.integrate.
    """
    steps: List[str] = []

    # constants
    if _is_const(e, x):
        steps.append(f"Constant rule: ∫{_pretty(e)} d{x} = {_pretty(e)}·{x}")
        return e * x, steps

    # the variable itself
    if e == x:
        steps.append(f"Power rule: ∫{x} d{x} = {x}**2/2")
        return x ** 2 / 2, steps

    # sum rule
    if isinstance(e, sp.Add):
        steps.append("Sum rule: ∫(a + b + …) = ∫a + ∫b + …")
        parts = []
        for term in e.args:
            it, st = _steps_int(term, x)
            steps.extend(st)
            parts.append(it)
        return sp.Add(*parts), steps

    # constant multiple rule
    if isinstance(e, sp.Mul):
        const_factors = [f for f in e.args if _is_const(f, x)]
        rest = [f for f in e.args if not _is_const(f, x)]
        if const_factors and rest:
            c = sp.Mul(*const_factors)
            inner = sp.Mul(*rest)
            steps.append(
                f"Constant multiple rule: ∫{_pretty(c)}·f({x}) d{x} = {_pretty(c)}·∫f({x}) d{x}")
            ii, st = _steps_int(inner, x)
            steps.extend(st)
            return c * ii, steps

    # power rule x^n (and the log special case)
    if isinstance(e, sp.Pow):
        base, exp = e.args
        if base == x and _is_const(exp, x):
            if exp == sp.Integer(-1):
                steps.append(f"Log rule: ∫1/{x} d{x} = log({x})")
                return sp.log(x), steps
            steps.append("Power rule: ∫x**n dx = x**(n+1)/(n+1)  (n ≠ -1)")
            return x ** (exp + 1) / (exp + 1), steps

    # standard function of the bare variable: sin, cos, exp, …
    if isinstance(e, sp.Function) and e.args and e.args[0] == x:
        anti = sp.integrate(e, x)
        steps.append(
            f"Standard integral: ∫{e.func.__name__}({x}) d{x} = {_pretty(anti)}")
        return anti, steps

    # fallback
    steps.append("Using SymPy integrate (fallback) for this structure.")
    return sp.integrate(e, x), steps


def integrate_steps(expr: str, wrt: str, a: Optional[str] = None, b: Optional[str] = None, variables: Optional[List[str]] = None) -> EngineResult:
    """
    Integral with lightweight steps. Definite integrals narrate the
    antiderivative rules, then apply the fundamental theorem; the RESULT
    always comes from sympy.integrate (safe for improper bounds).
    """
    wrt = _norm_wrt(wrt)
    locals_map = _symbols(variables or ([wrt] if wrt else []))
    e = _parse_expr(expr, locals_map)
    x = locals_map.get(wrt) if wrt in locals_map else sp.Symbol(wrt)

    anti, steps = _steps_int(e, x)
    anti = sp.simplify(anti)

    definite = a is not None and b is not None and str(a).strip() and str(b).strip()
    if definite:
        a_e = _parse_expr(str(a), locals_map)
        b_e = _parse_expr(str(b), locals_map)
        steps.append(f"Antiderivative: F({x}) = {_pretty(anti)} + C")
        steps.append(
            f"Fundamental theorem: ∫[{_pretty(a_e)}, {_pretty(b_e)}] = F({_pretty(b_e)}) − F({_pretty(a_e)})")
        r = sp.integrate(e, (x, a_e, b_e))
    else:
        steps.append("Add the constant of integration: + C")
        r = anti

    return EngineResult(text=str(r), latex=sp.latex(r), steps=steps)


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


def limit_steps(
    expr: str,
    wrt: str,
    limit_to: str,
    dir: Optional[str] = None,
    variables: Optional[List[str]] = None
) -> EngineResult:
    """
    Limit with a short narrative: direct substitution when it works,
    otherwise name the indeterminate form. The RESULT always comes from
    sympy.limit.
    """
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

    steps: List[str] = []
    side = {"": "", "+": " from the right", "-": " from the left"}[d]
    steps.append(f"Evaluate lim {x}→{_pretty(t)}{side} of {_pretty(e)}")

    # Try direct substitution first — the honest student move.
    try:
        direct = e.subs(x, t)
        if direct.has(sp.nan) or direct.has(sp.zoo):
            num, den = sp.fraction(sp.together(e))
            if den != 1:
                n0, d0 = num.subs(x, t), den.subs(x, t)
                form = f"{_pretty(n0)}/{_pretty(d0)}"
                steps.append(
                    f"Direct substitution gives {form} — indeterminate; "
                    "simplify/factor or use L'Hôpital (SymPy does this exactly).")
            else:
                steps.append(
                    "Direct substitution is undefined — evaluating the limit properly.")
        elif direct.is_finite is True:
            steps.append(f"Direct substitution works: {_pretty(direct)}")
        else:
            steps.append(
                "Substitution is not finite — analysing the behaviour near the point.")
    except Exception:
        steps.append("Direct substitution failed — evaluating the limit properly.")

    r = sp.limit(e, x, t, dir=d) if d in ("+", "-") else sp.limit(e, x, t)
    steps.append(f"Limit: {_pretty(r)}")
    return EngineResult(text=str(r), latex=sp.latex(r), steps=steps)


def _split_equation(expr: str) -> Optional[Tuple[str, str]]:
    """Split "lhs = rhs" only on a real assignment-style equals.

    A bare `"=" in expr` also fires on `>=`, `<=` and `!=`, splitting them into
    halves that do not parse. Those are relations, not equations to rearrange.
    """
    if not _PLAIN_EQUALS.search(expr):
        return None
    left, right = _PLAIN_EQUALS.split(expr, maxsplit=1)
    return left, right


def _as_equation(expr: str, locals_map: dict) -> sp.Equality:
    """Turn user text into the equation solve() should work on."""
    halves = _split_equation(expr)
    if halves is not None:
        return sp.Eq(_parse_expr(halves[0], locals_map),
                     _parse_expr(halves[1], locals_map))
    parsed = _parse_expr(expr, locals_map)
    # `Eq(x**2, 4)` IS already the equation. Wrapping it as Eq(<relation>, 0)
    # asks whether a truth value equals zero, which solves to nothing at all —
    # so `solve` on the documented `Eq(...)` form silently returned no roots.
    if isinstance(parsed, sp.Equality):
        return parsed
    return sp.Eq(parsed, 0)


def solve(expr: str, wrt: str, variables: Optional[List[str]] = None) -> EngineResult:
    wrt = _norm_wrt(wrt)
    locals_map = _symbols(variables or ([wrt] if wrt else []))
    equation = _as_equation(expr, locals_map)
    x = locals_map.get(wrt) if wrt in locals_map else sp.Symbol(wrt)
    r = sp.solve(equation, x)
    text = ", ".join([str(v) for v in r]) if isinstance(r, (list, tuple)) else str(r)
    return EngineResult(text=text, latex=sp.latex(r))


def solve_steps(expr: str, wrt: str, variables: Optional[List[str]] = None) -> EngineResult:
    """
    Solve with a short narrative: normalize to = 0, factor when it helps,
    name the quadratic formula when it applies. The RESULT always comes
    from sympy.solve.
    """
    wrt = _norm_wrt(wrt)
    locals_map = _symbols(variables or ([wrt] if wrt else []))
    equation = _as_equation(expr, locals_map)
    # The narrative rearranges to "... = 0", so lhs is the difference either way.
    lhs = sp.together(equation.lhs - equation.rhs)
    x = locals_map.get(wrt) if wrt in locals_map else sp.Symbol(wrt)

    steps: List[str] = [f"Rearrange to standard form: {_pretty(sp.expand(lhs))} = 0"]

    poly = lhs.as_poly(x) if lhs.free_symbols else None
    if poly is not None:
        deg = poly.degree()
        factored = sp.factor(lhs)
        if factored != lhs and isinstance(factored, sp.Mul):
            steps.append(f"Factor: {_pretty(factored)} = 0")
            steps.append("Zero-product rule: set each factor to 0.")
        elif deg == 2:
            a2, b2, c2 = poly.all_coeffs() if len(poly.all_coeffs()) == 3 else (None, None, None)
            if a2 is not None:
                disc = sp.simplify(b2 ** 2 - 4 * a2 * c2)
                steps.append(
                    f"Quadratic formula with a={_pretty(a2)}, b={_pretty(b2)}, "
                    f"c={_pretty(c2)}; discriminant = {_pretty(disc)}")
        elif deg is not None and deg > 2:
            steps.append(f"Degree-{deg} polynomial — solving exactly with SymPy.")
    else:
        steps.append("Not a polynomial in the unknown — solving exactly with SymPy.")

    r = sp.solve(equation, x)
    if isinstance(r, (list, tuple)):
        steps.append("Solutions: " + (", ".join(str(v) for v in r) if r else "none"))
        text = ", ".join(str(v) for v in r)
    else:
        steps.append(f"Solution: {r}")
        text = str(r)
    return EngineResult(text=text, latex=sp.latex(r), steps=steps)