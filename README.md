# SyMoNeuRaL RavenCalc

The calculus checker behind `www.symoneural.com`. FastAPI + SymPy; one
process serves the UI and the API on port 8000 (CPU only — it survives every
GPU handoff on the box by design); a Cloudflare Tunnel puts it on the public
host. Fully self-contained under this folder — a `run.sh` that resolves
relative to itself, finds a venv or falls back to system python,
self-installs deps, and serves.

The UI (`app/web/index.html`) wears the SyMoNeuRaL family skin — Studio's
design tokens, green-led. The two seasonal skins are preserved beside it
(`index.christmas.html`, `index.backtoschool.html`).

**Security note (read before exposing it):** `sympy.sympify` evals its input,
and this service once passed raw request bodies to it — a confirmed
unauthenticated RCE, fixed by an AST allowlist gate (`app/safe_parse.py`)
that every parse path routes through. 30+ adversarial tests pin the gate
shut. Details in that module's docstring.

## Run it

```bash
./run.sh                     # 127.0.0.1:8000
./run.sh --status            # is it already up?
./run.sh --port 8080
./run.sh --standalone        # SymPy only, no LLM (alias: --no-ai)
```

### First run: the API key

There is one setup step and `run.sh` will not start without it.

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put that value on the `RAVENCALC_API_KEY=` line in `.env`. **Never put a real
key in `.env.example`** - that file is committed, so anything in it is
published.

`run.sh` refuses to start when `.env` is missing, when the key is empty, or
when it still matches the value in `.env.example`. It used to copy
`.env.example` over to `.env` by itself and print a yellow warning, which meant
an unconfigured deploy came up serving `/api/compute` with whatever key was in
git. Failing closed is the point: without a key those routes answer 500 to
every caller anyway, so starting up "successfully" was only ever misleading.

### Host and port

`--host` / `--port` win, then the inherited environment (`RAVENCALC_HOST`,
`PORT`), then `.env`, then the defaults `127.0.0.1:8000`. `run.sh` and `run.py`
follow the same order, and neither overwrites a variable the caller already
exported - matching `load_dotenv()`, which does not either. (`PORT=8123
./run.sh` used to bind 8000 regardless, because `run.sh` set `PORT` after the
environment and then `.env` clobbered it a second time.)

## Standalone mode

RavenCalc does not need the LLM. **SymPy is the entire calculator** - every
answer and every step trace comes from `sympy_engine.py`, and the model is only
ever a front end that turns English into a `ComputeRequest`. So the useful
default when nothing else on the box is running is:

```bash
./run.sh --standalone
```

Equivalently `RAVENCALC_NO_AI=1` in `.env` or the environment. In that mode:

- `/`, `/health`, `/ui/compute`, `/api/compute` behave **identically**. Steps
  included.
- `/ui/ask` and `/api/ask` stay mounted and answer HTTP 200 with `ok: false`
  and a `detail` naming the reason, e.g. `AI unavailable: standalone mode
  (RAVENCALC_NO_AI)`. They are not 404s, so a caller learns why rather than
  guessing.
- `/ai/health` reports `ai_enabled: false`.

Nothing about the GPU, `make to-llm`, or a running engine is required, and no
`VLLM_API_KEY` is needed.

**Leaving the AI on while the LLM is down** degrades the same honest way. The
reachability check probes the engine's *authenticated* `/v1/models` rather than
its open `/health`, because `/health` answers 200 to anyone: the old check
passed whenever the process was alive and the real call then came back 401.
Now a missing key reports `AI unavailable: no API key` and a wrong one reports
that the key was rejected. Worth knowing: `LLM_API_KEY` falls back to
`VLLM_API_KEY`, which is exported from `~/.bashrc`, so anything started outside
a login shell has none - set `RAVENCALC_LLM_API_KEY` in `.env` instead.

## Endpoints

| Method | Path          | Auth                 | Used by            |
|--------|---------------|----------------------|--------------------|
| GET    | `/`           | none                 | the UI             |
| GET    | `/health`     | none                 | is it up           |
| POST   | `/ui/compute` | none, same-origin    | the browser (Raven)|
| POST   | `/api/compute`| `X-API-KEY` header   | scripts            |
| POST   | `/ui/ask`     | none, same-origin    | natural language   |
| POST   | `/api/ask`    | `X-API-KEY` header   | natural language   |
| GET    | `/ai/health`  | none                 | is the LLM usable  |

The `/ask` routes and `/ai/health` are the AI layer (`app/ai.py`) and are the
only ones that need the model.

Operations: `simplify` · `differentiate` · `integrate` · `limit` · `solve`.
Steps (the plain-English rule trace) exist for all four: derivatives
(sum/product/quotient/chain/power), integrals (sum, constant-multiple,
power, log, standard forms, plus the fundamental theorem for definite
bounds), solve (standard form, factoring + zero-product, quadratic formula
with discriminant) and limits (direct substitution, and it names the
indeterminate form when substitution fails).

## Cloudflare Tunnel

Existing ingress lives in `ops/cloudflared_config.yml` - it already points at
`http://127.0.0.1:8000`, so no reverse proxy (Apache/Caddy) is needed. Match
the tunnel name and credentials-file path to what Cloudflare has, then
`cloudflared tunnel run <name>` on the same host that runs `./run.sh`.

## Backend changes

Two, both in `app/main.py`. Everything else in the Christmas backend is
byte-identical.

**1. API-key enforcement (fixed 21 Jul 2026).** `/api/compute` now actually
enforces the key. `app/main.py` previously used `_=require_api_key` (a bare
default that never ran) instead of `_=Depends(require_api_key)`, so the
"protected" endpoint answered without a key. Fixed and verified: no key -> 401,
wrong key -> 401, correct `X-API-KEY` -> 200. Raven is unaffected either way
(the browser uses same-origin `/ui/compute`).

**2. Dead limit-direction fallback removed.** The limit branch read
`getattr(payload, "limit_dir", None) or getattr(payload, "dir", None)`.
`ComputeRequest` has no `dir` field and pydantic drops unknown keys, so the
second half could never return anything but `None` even when a caller sent
`dir` explicitly. Only the dead half was removed; one-sided limits still work
through `limit_dir` and through the inline `f(x), x->0+` form.

## Windows

The original `.bat` launcher and PowerShell scripts are under `_windows/` and
`scripts/`. On Linux use `run.sh`.

## Limits

Written plainly, on purpose.

- **The expression gate is an allowlist.** Only arithmetic over numbers,
  names, and a fixed list of mathematical functions parses. Anything else —
  attributes, strings, subscripts, keywords, comprehensions — is refused
  before SymPy ever sees it. If a legitimate function is missing, add it to
  `ALLOWED_CALLS` deliberately.
- **Very long flat sums are refused.** Python parses `a + b + c` as a chain,
  so a ~40+-term sum trips the same depth cap that stops nesting DoS. The cap
  cannot tell hostile nesting from a long homework polynomial; split the
  input instead.
- **One worker.** A pathological (but legal) SymPy computation can still take
  seconds; the rate limiter is the only backpressure.
- **The AI panel needs the local engine.** With symon down or the key absent
  it degrades to `--standalone` behaviour (SymPy only), and says so rather
  than failing.
- **`/ui/compute` is unauthenticated by design** (it is the public product);
  the gate + rate limit are its protection. The AI endpoints require the key.
