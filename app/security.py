import hmac

from fastapi import Header, HTTPException

from .config import settings


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-KEY")):
    if not settings.api_key:
        raise HTTPException(status_code=500, detail="Server API key not configured")
    # hmac.compare_digest, not `!=`: a plain string comparison returns as soon as
    # it reaches a differing byte, so how long the check takes leaks how much of
    # the key a guess got right. That reduces key recovery to a per-character
    # search rather than a search of the whole space. compare_digest takes the
    # same time whatever it is given.
    #
    # Compared as bytes because compare_digest rejects non-ASCII str and raises
    # TypeError — a header with an accented character would otherwise be a 500
    # instead of a 401.
    provided = (x_api_key or "").encode("utf-8", "surrogateescape")
    expected = settings.api_key.encode("utf-8", "surrogateescape")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
