"""An allowlist gate in front of SymPy's parser.

Why this file exists
--------------------
`sympy.sympify()` compiles and **evals** its input. SymPy says so itself:

    warning:: Note that this function uses ``eval``, and thus shouldn't be
    used on unsanitized input.

RavenCalc handed raw request bodies to `sympify`, and `/ui/compute` takes no
credential at all — only a rate limit. Confirmed over HTTP against a local
instance: posting ``__import__('os').getpid()`` returned the server's real PID.
That is unauthenticated remote code execution, not a hardening nicety.

Why NOT a denylist
------------------
Rejecting inputs that *contain* ``__`` / ``import`` / ``eval`` / ``os`` is the
obvious fix and it is the wrong one. It is a blocklist over an infinite space:

* ``getattr(x, chr(95)+chr(95)+'class'+chr(95)+chr(95))`` has no ``__`` in it.
* ``()  .__class__`` survives naive token checks; whitespace and comments do not
  have to look like the thing you are matching.
* Python source accepts unicode escapes in identifiers, so the literal bytes you
  grep for need never appear.
* Every new payload class needs a new rule, and you only learn you missed one
  from an incident.

It also blocks legitimate maths the moment a variable is called ``import_rate``.

So this module inverts it: parse to an AST, walk every node, and reject anything
not explicitly permitted. Unknown syntax fails closed. The gate runs *before*
SymPy sees the string, at the single choke point every engine entry point uses
(`sympy_engine._parse_expr`).

What survives the gate is arithmetic over numbers, names, and calls to a fixed
list of mathematical functions — which is the entire product.
"""
from __future__ import annotations

import ast

__all__ = ["UnsafeExpression", "validate_expression", "ALLOWED_CALLS"]

MAX_DEPTH = 40


class UnsafeExpression(ValueError):
    """Raised when an expression contains something outside the allowlist."""


# Callables a maths expression may name. Everything else — including builtins
# like getattr/eval/open and anything SymPy exposes that is not a function of
# numbers — is refused. Add to this list deliberately, never programmatically
# from a module's namespace.
ALLOWED_CALLS = frozenset({
    # trigonometric and inverse
    "sin", "cos", "tan", "sec", "csc", "cot",
    "asin", "acos", "atan", "atan2", "asec", "acsc", "acot",
    # hyperbolic and inverse
    "sinh", "cosh", "tanh", "sech", "csch", "coth",
    "asinh", "acosh", "atanh",
    # exponential, logarithmic, roots
    "exp", "log", "ln", "sqrt", "cbrt", "root", "Pow",
    # rounding, sign, magnitude
    "Abs", "abs", "sign", "floor", "ceiling", "frac", "Min", "Max",
    # combinatorics and special functions
    "factorial", "factorial2", "binomial", "gamma", "loggamma", "beta",
    "erf", "erfc", "zeta",
    # complex parts
    "re", "im", "conjugate", "arg",
    # relations, used by solve()
    "Eq", "Ne", "Lt", "Le", "Gt", "Ge",
    # structural: rationals and explicit constructors students type
    "Rational", "Integer", "Float", "S",
    # calculus written inline
    "diff", "integrate", "limit", "Sum", "Product", "series",
})

# Node types permitted anywhere in the tree. Deliberately excludes Attribute
# (kills `.__class__`, `os.system`), Subscript, Lambda, comprehensions,
# Starred, IfExp, NamedExpr (walrus), JoinedStr (f-strings), Dict, Set, Await
# and Yield.
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Call, ast.Name, ast.Load, ast.Constant,
    ast.Tuple, ast.List,
    # operators
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    # `^` is how students write exponentiation; SymPy reinterprets it. Allowed
    # because it is an arithmetic operator, not a capability.
    ast.BitXor,
    ast.USub, ast.UAdd,
    # comparisons, so `x**2 = 4`-style relations parse
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def _reject(node: ast.AST, why: str) -> None:
    raise UnsafeExpression(f"{why} is not allowed in an expression "
                           f"({type(node).__name__})")


def validate_expression(expr: str, *, max_len: int = 4000) -> None:
    """Raise UnsafeExpression unless `expr` is pure maths.

    Returns None on success; the caller then passes the ORIGINAL string to
    SymPy. This function never evaluates the expression.
    """
    if not isinstance(expr, str):
        raise UnsafeExpression("expression must be a string")
    if len(expr) > max_len:
        raise UnsafeExpression(f"expression longer than {max_len} characters")
    if "\x00" in expr:
        raise UnsafeExpression("expression contains a NUL byte")

    try:
        # Strip first: `ast.parse(" 4", mode="eval")` is an "unexpected indent"
        # SyntaxError, while sympify() happily accepts surrounding whitespace.
        # Without this, splitting "x**2 = 4" on the equals produced a valid
        # right-hand side that this gate then rejected. Leading/trailing
        # whitespace cannot change what an expression means, so removing it
        # narrows nothing.
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        # A parse failure is a client error, not a server one, and must not be
        # forwarded to sympify in the hope it copes.
        raise UnsafeExpression(f"could not parse the expression: {exc.msg}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            if isinstance(node, ast.Attribute):
                _reject(node, "attribute access")
            if isinstance(node, ast.Subscript):
                _reject(node, "indexing")
            if isinstance(node, (ast.Lambda, ast.IfExp)):
                _reject(node, "control flow")
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                                 ast.GeneratorExp)):
                _reject(node, "comprehensions")
            _reject(node, "this syntax")

        if isinstance(node, ast.Constant):
            # Numbers only. Refusing str/bytes alone defeats most payloads,
            # because `__import__('os')` needs a string argument.
            if isinstance(node.value, bool) or not isinstance(
                    node.value, (int, float, complex)):
                _reject(node, f"the literal {node.value!r}")

        if isinstance(node, ast.Name):
            if node.id.startswith("_"):
                # No dunders, no private names. This is the allowlist's version
                # of the denylist's `'__' in expr`, but it is enforced on a
                # parsed identifier rather than on raw text, so obfuscation
                # cannot slip past it.
                _reject(node, f"the name {node.id!r}")

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                _reject(node, "calling anything but a named function")
            if node.func.id not in ALLOWED_CALLS:
                raise UnsafeExpression(
                    f"{node.func.id!r} is not a permitted function; allowed: "
                    f"{', '.join(sorted(ALLOWED_CALLS))}")
            if node.keywords:
                _reject(node, "keyword arguments")

    depth = _depth(tree)
    if depth > MAX_DEPTH:
        # Deeply nested input makes SymPy do exponential work and can hang the
        # single worker; the rate limiter alone does not stop one bad request.
        raise UnsafeExpression(f"expression nests too deeply ({depth} levels)")


def _depth(node: ast.AST, level: int = 0) -> int:
    children = list(ast.iter_child_nodes(node))
    if not children:
        return level
    return max(_depth(child, level + 1) for child in children)
