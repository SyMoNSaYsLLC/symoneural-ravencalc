# SyMoNeuRaL • RavenCalc 🎄 (FastAPI + SymPy)

**Single site**: `https://www.symoneural.com` (served through Cloudflare Tunnel)

- One FastAPI app serves **both UI + API** on port **8000**
- UI is **friendly + simple** (no “API key” box)
- API key is **server-side** in `.env` and enforced on `/api/compute` (heartbeat.php style)
- UI uses `/ui/compute` (same-origin) so Raven never types keys

> Security note: Any browser client can inspect requests. The key is still useful to protect endpoints from random abuse,
but don’t treat it as “secret from the user”.

## Run (Windows)
1) Copy `.env.example` → `.env` and set `RAVENCALC_API_KEY`
2) Double-click: `start_ravencalc_www.bat`

Local:
- UI: http://127.0.0.1:8000
- Health: http://127.0.0.1:8000/health

## Cloudflare Tunnel (WWW ONLY)
Edit: `ops/cloudflared_config.yml`
- `credentials-file` path
- hostname (`www.symoneural.com`)

The start script runs:
`cloudflared.exe tunnel --config ops\cloudflared_config.yml run symoneural-main`

## Footprint
```
RavenCalcServer_Christmas_20251224-1007/
  app/
    main.py
    config.py
    security.py
    ratelimit.py
    schemas.py
    sympy_engine.py
    web/index.html
  ops/cloudflared_config.yml
  scripts/run_dev.bat
  scripts/run_dev.ps1
  scripts/run_dev.sh
  run.py
  requirements.txt
  start_ravencalc_www.bat
```
