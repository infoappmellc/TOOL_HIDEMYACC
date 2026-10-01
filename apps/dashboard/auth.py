from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from fastapi import Cookie, Header, HTTPException, status


COOKIE_NAME = "hma_session"


def _secret() -> bytes:
    return os.getenv("APP_SECRET", "dev-only-change-me").encode("utf-8")


def create_session(ttl_seconds: int = 86400) -> str:
    payload = json.dumps({"role": "admin", "exp": int(time.time()) + ttl_seconds}, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    signature = hmac.new(_secret(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def valid_session(token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(_secret(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        return payload.get("role") == "admin" and int(payload.get("exp", 0)) > int(time.time())
    except (ValueError, TypeError, json.JSONDecodeError):
        return False


def require_admin(hma_session: Optional[str] = Cookie(default=None)) -> None:
    if not valid_session(hma_session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chưa đăng nhập")


def require_worker(authorization: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("WORKER_TOKEN", "dev-worker-token")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Worker token không hợp lệ")

