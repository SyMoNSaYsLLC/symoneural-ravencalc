"""RavenCalc end to end: the maths, the auth, the rate limiter, the routes.

The service had no tests at all. These cover the parts where being wrong is
expensive rather than merely annoying:

* `/ui/compute` takes **no credential**. It is the front door for the browser
  UI, so it cannot be locked down without breaking the product — which is
  exactly why the parser gate has to hold. A regression here is an internet-
  facing hole, so the RCE payload is asserted against the real route.
* `require_api_key` guards `/api/compute`, the one AI_Local_NVIDIA calls.
* The rate limiter's table used to grow forever, keyed by client IP.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ratelimit import RateLimiter  # noqa: E402
from app.sympy_engine import (  # noqa: E402
    differentiate,
    integrate,
    limit,
    simplify,
    solve,
)

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="needs httpx for TestClient")

API_KEY = "test-key-not-a-real-one"


# --- the maths ---------------------------------------------------------------

@pytest.mark.parametrize("expr,expected", [
    ("x**2 + 3*x - 1", "x**2 + 3*x - 1"),
    ("sin(x)**2 + cos(x)**2", "1"),
    ("(x^2-1)/(x-1)", "x + 1"),
    ("Rational(1,3) + Rational(1,6)", "1/2"),
])
def test_simplify(expr, expected):
    assert simplify(expr).text == expected


def test_differentiate_and_integrate_round_trip():
    assert differentiate("x**3", "x").text == "3*x**2"
    assert integrate("3*x**2", "x").text == "x**3"


def test_definite_integral():
    assert integrate("x**2", "x", "0", "1").text == "1/3"


def test_limit_of_sin_over_x():
    assert limit("sin(x)/x", "x", "0").text == "1"


@pytest.mark.parametrize("equation,roots", [
    ("x**2 - 4", "-2, 2"),
    ("x**2 = 4", "-2, 2"),
    ("2*x + 6 = 0", "-3"),
    ("x^2-1=0", "-1, 1"),
])
def test_solve_accepts_every_documented_equation_form(equation, roots):
    assert solve(equation, "x").text == roots


def test_DEFECT_solve_accepts_the_Eq_form():
    """`Eq(x**2, 4)` used to return NO roots. solve() split on "=", found none,
    and wrapped the whole thing as Eq(<relation>, 0) — asking whether a truth
    value equals zero. Silent: a valid question answered "no solutions"."""
    assert solve("Eq(x**2, 4)", "x").text == "-2, 2"


def test_DEFECT_a_relational_operator_is_not_split_as_an_equation():
    """`>=` contains "=", so splitting on a bare "=" tore `x >= 2` into `x >`
    and ` 2`, neither of which parses."""
    for relation in ("x >= 2", "x <= 2", "x != 2"):
        result = solve(relation, "x")
        assert isinstance(result.text, str)


# --- the parser gate, through the real HTTP routes ---------------------------

@pytest.fixture
def client(monkeypatch):
    from app import config, security
    monkeypatch.setattr(config.settings, "api_key", API_KEY)
    monkeypatch.setattr(security.settings, "api_key", API_KEY)
    from app import main
    return fastapi_testclient.TestClient(main.app)


def test_the_ui_route_needs_no_credential():
    """Stated as a test because the gate's whole justification rests on it: this
    route is reachable by anyone who can reach the port."""
    import inspect

    from app import main
    assert "require_api_key" not in inspect.signature(main.ui_compute).parameters
    assert "require_api_key" in str(inspect.signature(main.api_compute))


def test_DEFECT_the_rce_payload_is_refused_on_the_unauthenticated_route(client):
    """The confirmed vulnerability, at the layer an attacker actually touches.
    Before the fix this returned the server's real PID with a 200."""
    r = client.post("/ui/compute", json={
        "operation": "simplify", "expression": "__import__('os').getpid()"})
    assert r.status_code == 400, r.text
    assert "getpid" not in r.text.replace("__import__('os').getpid()", "")


def test_the_ui_route_still_does_maths(client):
    r = client.post("/ui/compute", json={
        "operation": "simplify", "expression": "sin(x)**2 + cos(x)**2"})
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "1"


def test_the_api_route_requires_the_key(client):
    payload = {"operation": "simplify", "expression": "1+1"}
    assert client.post("/api/compute", json=payload).status_code == 401
    assert client.post("/api/compute", json=payload,
                       headers={"X-API-KEY": "wrong"}).status_code == 401
    ok = client.post("/api/compute", json=payload, headers={"X-API-KEY": API_KEY})
    assert ok.status_code == 200, ok.text
    assert ok.json()["result"] == "2"


def test_health_is_open():
    from app import main
    assert fastapi_testclient.TestClient(main.app).get("/health").status_code == 200


# --- auth ------------------------------------------------------------------

def test_key_comparison_is_constant_time():
    """`!=` returns at the first differing byte, so response time leaks how much
    of the key a guess matched — turning recovery into a per-character search."""
    import inspect

    from app import security
    src = inspect.getsource(security.require_api_key)
    assert "compare_digest" in src
    assert "x_api_key != " not in src


def test_an_unconfigured_key_fails_closed(monkeypatch):
    """No key configured must be a 500, never an open door."""
    from fastapi import HTTPException

    from app import security
    monkeypatch.setattr(security.settings, "api_key", "")
    with pytest.raises(HTTPException) as exc:
        security.require_api_key("anything")
    assert exc.value.status_code == 500


def test_a_non_ascii_header_is_a_401_not_a_crash(monkeypatch):
    """compare_digest raises TypeError on non-ASCII str, which would surface as
    a 500 and hand an attacker a way to tell inputs apart."""
    from fastapi import HTTPException

    from app import security
    monkeypatch.setattr(security.settings, "api_key", API_KEY)
    with pytest.raises(HTTPException) as exc:
        security.require_api_key("ümlaut-key")
    assert exc.value.status_code == 401


# --- rate limiter -----------------------------------------------------------

def test_the_bucket_allows_a_burst_then_refuses():
    limiter = RateLimiter(rate_per_min=5)
    assert all(limiter.allow("ip") for _ in range(5))
    assert not limiter.allow("ip")


def test_buckets_are_per_key():
    limiter = RateLimiter(rate_per_min=1)
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")


def test_DEFECT_the_table_is_bounded():
    """It was an unbounded dict keyed by client IP with nothing ever removed, so
    a caller rotating source addresses grew it until the worker died — a denial
    of service the rate limit itself cannot stop, because every single request
    is individually allowed."""
    limiter = RateLimiter(rate_per_min=60)
    limiter.MAX_KEYS = 100
    for i in range(1000):
        limiter.allow(f"10.0.{i // 256}.{i % 256}")
    assert len(limiter.state) <= limiter.MAX_KEYS, len(limiter.state)


def test_eviction_does_not_forget_an_active_caller():
    """A key still being used inside its window must keep its bucket, or the
    limit stops limiting."""
    limiter = RateLimiter(rate_per_min=2)
    limiter.MAX_KEYS = 10
    assert limiter.allow("busy")
    assert limiter.allow("busy")
    for i in range(50):
        limiter.allow(f"other-{i}")
    assert not limiter.allow("busy"), "an active caller's bucket was dropped"


def test_a_fully_refilled_bucket_is_reclaimed():
    """Once a bucket has refilled it is indistinguishable from a new caller, so
    holding the row buys nothing."""
    limiter = RateLimiter(rate_per_min=60)
    limiter.allow("gone")
    assert "gone" in limiter.state
    # Backdate past the full-refill horizon rather than sleeping for it.
    tokens, _ = limiter.state["gone"]
    limiter.state["gone"] = (tokens, time.time() - limiter._full_after - 1)
    limiter._evict(time.time())
    assert "gone" not in limiter.state
