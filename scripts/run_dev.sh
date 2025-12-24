#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv 2>/dev/null || python -m venv .venv
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; else PY=".venv/Scripts/python.exe"; fi
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements.txt
"$PY" run.py
