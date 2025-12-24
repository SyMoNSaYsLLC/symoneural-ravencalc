import os

def getenv_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default

class Settings:
    api_key: str = os.getenv("RAVENCALC_API_KEY", "").strip()
    max_expr_len: int = getenv_int("RAVENCALC_MAX_EXPR_LEN", 4000)
    rate_per_min: int = getenv_int("RAVENCALC_RATE_PER_MIN", 60)
    public_host: str = os.getenv("RAVENCALC_PUBLIC_HOST", "http://127.0.0.1:8000").strip()

settings = Settings()
