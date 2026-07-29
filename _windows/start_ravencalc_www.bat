@echo off
setlocal

REM ===============================
REM  SyMoNeuRaL RavenCalc Launcher
REM ===============================

REM --- Project root ---
cd /d "C:\Users\Garrett\RavenCalcServer_Christmas_20251224-1007"

REM --- Start RavenCalc API (FastAPI / Uvicorn) ---
start "RavenCalc API" cmd /k ^
  ".venv\Scripts\python.exe" run.py

REM --- Start Cloudflare Tunnel (symoneural-main) ---
start "cloudflared symoneural-main" cmd /k ^
  "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel run symoneural-main

endlocal