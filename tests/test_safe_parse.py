"""The allowlist gate in front of SymPy's eval-based parser.

RavenCalc passed request bodies straight to `sympy.sympify`, which SymPy's own
docstring says uses `eval` and "shouldn't be used on unsanitized input". Since
`/ui/compute` takes no credential — only a rate limit — that was unauthenticated
remote code execution. Confirmed over HTTP against a local instance before the
fix: posting `__import__('os').getpid()` returned the server's real PID.

These tests exist in two halves and BOTH matter. The attack half stops the hole
reopening. The maths half stops the fix being "reject everything", which would
pass every security test and ship a broken calculator — the first version of the
gate did exactly that to `x**2 = 4`, and only the maths half caught it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.safe_parse import (  # noqa: E402
    ALLOWED_CALLS,
    UnsafeExpression,
    validate_expression,
)

# Each of these executed, or opened the door to executing, arbitrary Python
# before the gate existed.
ATTACKS = [
    pytest.param("__import__('os').getpid()", id="the confirmed RCE"),
    pytest.param("__import__('os').system('id')", id="command execution"),
    pytest.param("eval('1+1')", id="eval"),
    pytest.param("exec('x=1')", id="exec"),
    pytest.param("open('/etc/passwd').read()", id="file read"),
    pytest.param("getattr(x, '__class__')", id="getattr"),
    pytest.param("().__class__.__bases__[0]", id="dunder walk"),
    pytest.param("x.__dict__", id="attribute access"),
    pytest.param("[c for c in ().__class__.__mro__]", id="comprehension"),
    pytest.param("lambda: 1", id="lambda"),
    pytest.param("globals()", id="globals"),
    pytest.param("vars()", id="vars"),
    pytest.param("compile('1','','eval')", id="compile"),
    pytest.param("x if x else 1", id="conditional expression"),
    pytest.param("(y := 2)", id="walrus"),
    pytest.param("f'{x}'", id="f-string"),
    pytest.param("x[0]", id="subscript"),
    pytest.param("{'a': 1}", id="dict literal"),
    # The one that matters most against a substring denylist: no literal "__"
    # and no literal "import" appear anywhere in the source text.
    pytest.param("getattr(x, chr(95)+chr(95)+'class'+chr(95)+chr(95))",
                 id="obfuscated, defeats a denylist"),
]

# Real inputs from the README, the UI examples and ordinary schoolwork.
LEGITIMATE = [
    "x**2 + 3*x - 1",
    "(x^2-1)/(x-1)",
    "sin(x)/x",
    "sin(x)**2 + cos(x)**2",
    "sqrt(x**2+1)",
    "exp(-x)*log(x)",
    "pi*r**2",
    "Eq(x**2, 4)",
    "factorial(5)",
    "Abs(-3)",
    "Rational(1,3) + Rational(1,6)",
    "log(x)/log(10)",
    "atan2(y, x)",
    "Min(x, y)",
    "3.5*x - 2/7",
    "-x**-2",
    "x*(y + z)/(2*w)",
    "  x**2 + 1  ",
]


@pytest.mark.parametrize("payload", ATTACKS)
def test_attacks_are_refused(payload):
    with pytest.raises(UnsafeExpression):
        validate_expression(payload)


@pytest.mark.parametrize("expr", LEGITIMATE)
def test_real_maths_is_accepted(expr):
    validate_expression(expr)


def test_string_literals_are_refused_at_all():
    """Not cosmetic. `__import__('os')` needs a string argument, so refusing str
    constants outright removes the argument every import-based payload needs."""
    with pytest.raises(UnsafeExpression):
        validate_expression("'os'")
    with pytest.raises(UnsafeExpression):
        validate_expression("sqrt('4')")


def test_leading_underscore_names_are_refused():
    for name in ("_x", "__x", "_", "__builtins__"):
        with pytest.raises(UnsafeExpression):
            validate_expression(name)


def test_unknown_functions_are_refused_even_if_harmless_sounding():
    """The allowlist is a list, not a heuristic: a name nobody vetted is out,
    which is what makes the gate hold as SymPy's namespace changes."""
    for call in ("system(1)", "print(1)", "input()", "sympify('1')", "Symbol('x')"):
        with pytest.raises(UnsafeExpression):
            validate_expression(call)


def test_the_allowlist_has_no_dangerous_entries():
    """A guard on the guard: if someone widens ALLOWED_CALLS, these names must
    never be what they add."""
    forbidden = {"eval", "exec", "compile", "getattr", "setattr", "delattr",
                 "open", "input", "print", "globals", "locals", "vars",
                 "__import__", "sympify", "system", "Symbol", "srepr"}
    assert not (ALLOWED_CALLS & forbidden)


def test_over_long_input_is_refused_before_parsing():
    with pytest.raises(UnsafeExpression, match="longer than"):
        validate_expression("1+" * 5000 + "1", max_len=100)


def test_bare_equations_are_not_valid_python_and_are_split_upstream():
    """`x**2 = 4` is an assignment, not an expression, so it cannot survive
    `ast.parse(mode="eval")` — and it should not: the engine splits on the
    equals sign first and validates each side. Asserted here so nobody "fixes"
    the gate by loosening it to accept assignment syntax."""
    for equation in ("x**2 = 4", "2*x + 6 = 0"):
        with pytest.raises(UnsafeExpression):
            validate_expression(equation)


def test_deep_nesting_is_refused():
    """SymPy does superlinear work on deeply nested input and the service has one
    worker, so a single request could hang it. The rate limiter does not help:
    the request is individually legal.

    Nested CALLS, not nested parentheses — `((((x))))` is just `x` once parsed,
    because redundant parens create no AST nodes at all.
    """
    with pytest.raises(UnsafeExpression, match="deeply"):
        validate_expression("sin(" * 60 + "x" + ")" * 60)


def test_ordinary_nesting_depth_is_fine():
    """The depth limit must not clip real work: a handful of nested functions and
    a long polynomial are both normal."""
    validate_expression("sin(cos(tan(sqrt(x**2 + 1))))")
    validate_expression(" + ".join(f"{c}*x**{c}" for c in range(1, 30)))


def test_a_nul_byte_is_refused():
    with pytest.raises(UnsafeExpression):
        validate_expression("x\x00+1")


def test_a_syntax_error_is_a_client_error_not_a_pass_through():
    """Unparseable input must stop here rather than being handed to sympify in
    the hope it copes — sympify's coping is the eval."""
    with pytest.raises(UnsafeExpression, match="could not parse"):
        validate_expression("x +* 2")


def test_validation_never_evaluates():
    """The gate must not itself run the thing it is inspecting. If it did, the
    check would be the vulnerability."""
    marker = Path(__file__).parent / "_should_not_exist.txt"
    if marker.exists():
        marker.unlink()
    with pytest.raises(UnsafeExpression):
        validate_expression(f"open({str(marker)!r}, 'w')")
    assert not marker.exists()


# Added 2026-08-08 for the release pass: exotic vectors the walker refuses by
# design. Pinned so a future refactor (say, swapping ast.walk for a visitor)
# cannot quietly widen the gate.
EXTRA_ATTACKS = [
    # Python normalizes identifiers to NFKC, so a FULLWIDTH `eval` becomes the
    # real one after parsing. The gate sees the normalized Name and refuses —
    # exactly the class of trick a raw-text denylist can never catch.
    pytest.param("ｅｖａｌ('1+1')", id="NFKC fullwidth eval"),
    pytest.param("sin(x).__class__", id="attribute on a call result"),
    pytest.param("sin(*x)", id="starred argument"),
    pytest.param("sin(x, evaluate=1)", id="keyword argument"),
    pytest.param("x and y", id="boolean operator"),
    pytest.param("b'os'", id="bytes literal"),
    pytest.param("{1, 2}", id="set literal"),
    pytest.param("True", id="bool literal"),
    pytest.param("x[0:2]", id="slice"),
]


@pytest.mark.parametrize("payload", EXTRA_ATTACKS)
def test_exotic_vectors_are_refused(payload):
    with pytest.raises(UnsafeExpression):
        validate_expression(payload)


def test_long_flat_sums_hit_the_depth_cap_by_design():
    # There is no "wide" in Python's AST: `a + b + c` parses left-associative,
    # so an n-term sum is an (n-1)-deep BinOp chain. A ~30-term sum passes;
    # a 200-term one trips the same depth cap that stops nesting DoS. That is
    # a deliberate trade (documented in the README's Limits) — the cap cannot
    # tell hostile nesting from a very long homework polynomial.
    validate_expression(" + ".join(["x"] * 30))  # fine
    with pytest.raises(UnsafeExpression, match="nests too deeply"):
        validate_expression(" + ".join(["x"] * 200))


def test_oversized_via_default_cap_matches_config():
    from app.config import settings
    too_long = "x + " * settings.max_expr_len
    with pytest.raises(UnsafeExpression, match="longer than"):
        validate_expression(too_long, max_len=settings.max_expr_len)
