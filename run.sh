#!/usr/bin/env bash
# Serve RavenCalc - the calculus checker behind www.symoneural.com.
#
# FastAPI + SymPy. One process serves both the UI and the API on port 8000;
# a Cloudflare Tunnel puts it on the public host. The backend is the Christmas
# 2025 build, byte-for-byte untouched - only the UI is the back-to-school skin.
# Resolves everything relative to this script, so the folder can live anywhere:
#
#   ./run.sh                     serve on 127.0.0.1:8000
#   ./run.sh --port 8080
#   ./run.sh --host 0.0.0.0      LAN (no app auth on /ui - use the tunnel)
#   ./run.sh --status            is it already up?

set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HOST=127.0.0.1
PORT=8000
STATUS_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)    PORT="$2"; shift 2 ;;
        --host)    HOST="$2"; shift 2 ;;
        --status)  STATUS_ONLY=1; shift ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

red() { printf '\033[31m%s\033[0m\n' "$*"; }
grn() { printf '\033[32m%s\033[0m\n' "$*"; }
ylw() { printf '\033[33m%s\033[0m\n' "$*"; }

# --- status -----------------------------------------------------------------

if [[ $STATUS_ONLY -eq 1 ]]; then
    if curl -fsS --max-time 3 --noproxy '*' "http://$HOST:$PORT/health" >/dev/null 2>&1; then
        grn "RavenCalc is up on http://$HOST:$PORT"
        exit 0
    fi
    ylw "Nothing responding on http://$HOST:$PORT"
    exit 1
fi

# --- python -----------------------------------------------------------------
# Prefer this folder's own venv, else whatever python is on PATH. No parent
# references - everything stays inside tools/ravencalc. The bundled .venv is a
# Windows tree (Scripts/, no bin/python) and is skipped by the -x test, so a
# fresh Linux venv at .venv/ (or system python) is used instead.
if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
else
    PY="$(command -v python3 || command -v python)"
fi

if ! "$PY" -c "import fastapi, uvicorn, sympy, dotenv" 2>/dev/null; then
    echo "Installing dependencies with $PY ..."
    "$PY" -m pip install -q -r requirements.txt
fi

# --- config -----------------------------------------------------------------
# app/config.py reads os.environ at import, so load .env into the environment
# before uvicorn imports the app. run.py did this via python-dotenv; we do the
# same here so --host/--port are honoured without editing the app.
if [[ ! -f .env ]]; then
    ylw ".env not found - copying .env.example."
    ylw "Set RAVENCALC_API_KEY before exposing /api/compute."
    cp .env.example .env
fi
set -a; . ./.env; set +a

# --- preflight --------------------------------------------------------------

if curl -fsS --max-time 2 --noproxy '*' "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    ylw "Something is already serving on port $PORT."
    echo "Check it with:  ./run.sh --status"
    exit 1
fi

# --- serve ------------------------------------------------------------------

# app/ai.py imports app/main.py and adds the natural-language routes (/ui/ask,
# /api/ask) to that same FastAPI instance, so this serves both. The Christmas
# backend is untouched either way -- RAVENCALC_NO_AI=1 serves it bare.
if [[ -n "${RAVENCALC_NO_AI:-}" ]]; then
    TARGET="app.main:app"
    AI_NOTE="natural-language input: off"
else
    TARGET="app.ai:app"
    AI_NOTE="natural-language input: on (LLM at ${RAVENCALC_LLM_URL:-http://localhost:9001})"
fi

echo "Serving RavenCalc (back-to-school UI)"
echo "  http://$HOST:$PORT   backend: Christmas 2025 (untouched)"
echo "  $AI_NOTE"
echo

exec "$PY" -m uvicorn "$TARGET" --host "$HOST" --port "$PORT"
