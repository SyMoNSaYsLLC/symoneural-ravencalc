# RavenCalc (back-to-school edition)

The calculus checker behind `www.symoneural.com`, wired into SyOsML the same
way as `tools/ml/studio`: a `run.sh` that resolves relative to itself, finds a
venv or falls back to system python, self-installs deps, and serves. Fully
self-contained under this folder - no parent-directory references.

FastAPI + SymPy. One process serves the UI and the API on port 8000; a
Cloudflare Tunnel puts it on the public host. **The backend is the Christmas
2025 build, byte-for-byte untouched** - `main.py`, `sympy_engine.py`,
`schemas.py`, `config.py`, `security.py`, `ratelimit.py` are exactly as they
shipped. The only change is the UI (`app/web/index.html`), now the amber
back-to-school skin with the steps checkbox on by default. The Christmas UI is
kept beside it as `app/web/index.christmas.html`.

## Run it

```bash
./run.sh                     # 127.0.0.1:8000
./run.sh --status            # is it already up?
./run.sh --port 8080
```

First run copies `.env.example` -> `.env`. Set `RAVENCALC_API_KEY` to a long
random string before anything hits `/api/compute` from outside the page.

## Endpoints

| Method | Path          | Auth                 | Used by            |
|--------|---------------|----------------------|--------------------|
| GET    | `/`           | none                 | the UI             |
| GET    | `/health`     | none                 | is it up           |
| POST   | `/ui/compute` | none, same-origin    | the browser (Raven)|
| POST   | `/api/compute`| `X-API-KEY` header   | scripts            |

Operations: `simplify` · `differentiate` · `integrate` · `limit` · `solve`.
Steps (the plain-English rule trace) exist for derivatives.

## Cloudflare Tunnel

Existing ingress lives in `ops/cloudflared_config.yml` - it already points at
`http://127.0.0.1:8000`, so no reverse proxy (Apache/Caddy) is needed. Match
the tunnel name and credentials-file path to what Cloudflare has, then
`cloudflared tunnel run <name>` on the same host that runs `./run.sh`.

## API-key enforcement (fixed 21 Jul 2026)

`/api/compute` now actually enforces the key. `app/main.py` previously used
`_=require_api_key` (a bare default that never ran) instead of
`_=Depends(require_api_key)`, so the "protected" endpoint answered without a
key. Fixed and verified: no key -> 401, wrong key -> 401, correct
`X-API-KEY` -> 200. Raven is unaffected either way (the browser uses
same-origin `/ui/compute`). This is the one intentional backend change from
the byte-identical Christmas build.

## Windows

The original `.bat` launcher and PowerShell scripts are under `_windows/` and
`scripts/`. On Linux use `run.sh`.
