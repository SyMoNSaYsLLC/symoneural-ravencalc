#!/usr/bin/env bash
# Serve RavenCalc - the calculus checker behind www.symoneural.com.
#
# FastAPI + SymPy. One process serves both the UI and the API; a Cloudflare
# Tunnel puts it on the public host. Resolves everything relative to this
# script, so the folder can live anywhere:
#
#   ./run.sh                     serve on 127.0.0.1:8000
#   ./run.sh --port 8080
#   ./run.sh --host 0.0.0.0      LAN (no app auth on /ui - use the tunnel)
#   ./run.sh --standalone        SymPy only, no LLM (alias: --no-ai)
#   ./run.sh --status            is it already up?
#
# Config precedence matches run.py: the flags above win, then the inherited
# environment, then .env, then these defaults. The environment names are PORT
# and RAVENCALC_HOST.

set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Empty until a flag sets one, so we can tell "not given" from "given 8000"
# and keep the flag ahead of the environment.
CLI_HOST=
CLI_PORT=
STATUS_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)    CLI_PORT="$2"; shift 2 ;;
        --host)    CLI_HOST="$2"; shift 2 ;;
        --status)  STATUS_ONLY=1; shift ;;
        --standalone|--no-ai) export RAVENCALC_NO_AI=1; shift ;;
        -h|--help) sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

red() { printf '\033[31m%s\033[0m\n' "$*"; }
grn() { printf '\033[32m%s\033[0m\n' "$*"; }
ylw() { printf '\033[33m%s\033[0m\n' "$*"; }

# --- config -----------------------------------------------------------------
# app/config.py reads os.environ at import, so .env has to be in the
# environment before uvicorn imports the app. run.py does this with
# python-dotenv, whose load_dotenv() does NOT overwrite a variable that is
# already set; we match that, so "PORT=8123 ./run.sh" and "PORT=8123 python
# run.py" agree. The old "set -a; . ./.env" clobbered the caller instead, which
# is why an inherited PORT used to be ignored.
load_env() {
    local line name val
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"                     # .env is often written on Windows
        if [[ "$line" =~ ^[[:space:]]*(#|$) ]]; then
            continue
        fi
        if [[ ! "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]]; then
            continue
        fi
        name="${BASH_REMATCH[2]}"
        val="${BASH_REMATCH[3]}"
        val="${val#[\"\']}"; val="${val%[\"\']}"
        if [[ -n "${!name+x}" ]]; then
            continue                             # already in the environment: it wins
        fi
        export "$name=$val"
    done < .env
}

if [[ -f .env ]]; then
    load_env
fi

HOST="${CLI_HOST:-${RAVENCALC_HOST:-127.0.0.1}}"
PORT="${CLI_PORT:-${PORT:-8000}}"

# --- status -----------------------------------------------------------------
# Deliberately above the .env checks below: asking "is it up?" should work on a
# machine that has never been configured.

if [[ $STATUS_ONLY -eq 1 ]]; then
    if curl -fsS --max-time 3 --noproxy '*' "http://$HOST:$PORT/health" >/dev/null 2>&1; then
        grn "RavenCalc is up on http://$HOST:$PORT"
        exit 0
    fi
    ylw "Nothing responding on http://$HOST:$PORT"
    exit 1
fi

# --- API key: fail closed ---------------------------------------------------
# .env.example is committed, so any value in it is published by construction.
# This used to copy .env.example to .env automatically and warn in yellow,
# which meant a fresh deploy came up serving /api/compute with a key anyone
# could read out of git. Refuse instead: an unconfigured deploy should not
# start at all rather than start unprotected.
KEYGEN='python3 -c "import secrets; print(secrets.token_urlsafe(32))"'
EXAMPLE_KEY="$(sed -n 's/^RAVENCALC_API_KEY=//p' .env.example 2>/dev/null | head -n1 || true)"

key_help() {
    echo
    echo "Generate one:"
    echo "  $KEYGEN"
    echo
    echo "then put it on the RAVENCALC_API_KEY= line in .env (never in .env.example,"
    echo "which is committed)."
}

if [[ ! -f .env ]]; then
    red "No .env - refusing to start."
    echo "RavenCalc needs its own API key to guard /api/compute and /api/ask."
    echo
    echo "  cp .env.example .env"
    key_help
    exit 1
fi

if [[ -z "${RAVENCALC_API_KEY:-}" ]]; then
    red "RAVENCALC_API_KEY is empty in .env - refusing to start."
    echo "/api/compute and /api/ask would answer 500 to every caller."
    key_help
    exit 1
fi

if [[ -n "$EXAMPLE_KEY" && "$RAVENCALC_API_KEY" == "$EXAMPLE_KEY" ]]; then
    red "RAVENCALC_API_KEY is still the .env.example value - refusing to start."
    echo "That value is committed to git, so it protects nothing."
    key_help
    exit 1
fi

# --- python -----------------------------------------------------------------
# Prefer this folder's own venv, else whatever python is on PATH. No parent
# references - everything stays inside this directory. The bundled .venv from
# the Windows build is a Scripts/ tree with no bin/python and is skipped by the
# -x test, so a fresh Linux venv at .venv/ (or system python) is used instead.
if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
else
    PY="$(command -v python3 || command -v python)"
fi

if ! "$PY" -c "import fastapi, uvicorn, sympy, dotenv" 2>/dev/null; then
    echo "Installing dependencies with $PY ..."
    "$PY" -m pip install -q -r requirements.txt
fi

# --- preflight --------------------------------------------------------------

if curl -fsS --max-time 2 --noproxy '*' "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    ylw "Something is already serving on port $PORT."
    echo "Check it with:  ./run.sh --status"
    exit 1
fi

# --- serve ------------------------------------------------------------------

# app/ai.py imports app/main.py and adds the natural-language routes (/ui/ask,
# /api/ask, /ai/health) to that same FastAPI instance, so this serves both, and
# main.py's compute routes are identical either way. We serve it even in
# standalone mode: with RAVENCALC_NO_AI set the ask routes stay mounted and
# answer "the AI layer is off" instead of 404ing, which is the difference
# between a documented mode and a mystery.
if [[ -n "${RAVENCALC_NO_AI:-}" ]]; then
    AI_NOTE="natural-language input: off (standalone - SymPy only)"
else
    AI_NOTE="natural-language input: on (LLM at ${RAVENCALC_LLM_URL:-http://localhost:9001})"
fi

echo "Serving RavenCalc (back-to-school UI)"
echo "  http://$HOST:$PORT"
echo "  $AI_NOTE"
echo

exec "$PY" -m uvicorn app.ai:app --host "$HOST" --port "$PORT"
