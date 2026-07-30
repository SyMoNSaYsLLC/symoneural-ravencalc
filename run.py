"""Serve RavenCalc. The Windows launcher (_windows/) calls this; on Linux
run.sh is the front door. Both honour the same contract: the inherited
environment wins over .env (load_dotenv does not override), RAVENCALC_HOST and
PORT name the bind address, and an unconfigured API key refuses to start.
"""

from dotenv import load_dotenv
load_dotenv()

import os
import sys
from pathlib import Path

import uvicorn

# app.ai imports app.main and adds the natural-language routes to that same
# FastAPI instance, so this serves everything and main.py's compute routes are
# identical either way. Served even in standalone mode: with RAVENCALC_NO_AI
# set the ask routes stay mounted and report that the AI layer is off rather
# than 404ing. See "Standalone mode" in the README.
TARGET = "app.ai:app"

HERE = Path(__file__).resolve().parent

KEYGEN = 'python3 -c "import secrets; print(secrets.token_urlsafe(32))"'


def _example_key() -> str:
    """The RAVENCALC_API_KEY value committed in .env.example, if any.

    .env.example is tracked, so whatever is in it is public. Read it rather
    than hardcoding a value here -- this file is tracked too.
    """
    try:
        for line in (HERE / ".env.example").read_text().splitlines():
            if line.startswith("RAVENCALC_API_KEY="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _require_api_key() -> None:
    """Refuse to boot unconfigured. /api/compute and /api/ask answer 500 to
    every caller without a key, and an example key protects nothing."""
    key = os.environ.get("RAVENCALC_API_KEY", "").strip()
    example = _example_key()

    if not (HERE / ".env").exists():
        problem = "No .env next to run.py."
    elif not key:
        problem = "RAVENCALC_API_KEY is empty."
    elif example and key == example:
        problem = "RAVENCALC_API_KEY is still the .env.example value, which is committed to git."
    else:
        return

    print(f"{problem} Refusing to start.", file=sys.stderr)
    print("", file=sys.stderr)
    print("  cp .env.example .env", file=sys.stderr)
    print(f"  {KEYGEN}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Put that value on the RAVENCALC_API_KEY= line in .env -- never in", file=sys.stderr)
    print(".env.example, which is committed.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    _require_api_key()
    uvicorn.run(
        TARGET,
        host=os.environ.get("RAVENCALC_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
