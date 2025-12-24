from fastapi import Header, HTTPException
from .config import settings

def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-KEY")):
    if not settings.api_key:
        raise HTTPException(status_code=500, detail="Server API key not configured")
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
