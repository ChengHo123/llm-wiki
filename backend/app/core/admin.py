"""Admin 鑑權：HMAC-signed cookie，密鑰用 ADMIN_PASSWORD。

無需資料庫 / 額外 table，登入後寫一個 24 小時的 cookie，內容是 HMAC 簽過的時戳 payload。
"""
import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode

from fastapi import Cookie, HTTPException

from app.core.config import get_settings

ADMIN_COOKIE_NAME = "admin_session"
ADMIN_TOKEN_TTL = 60 * 60 * 24  # 24 hours


def _sign(payload_b64: str, secret: str) -> str:
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    return urlsafe_b64encode(sig).decode().rstrip("=")


def _b64encode(data: bytes) -> str:
    return urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return urlsafe_b64decode(s + pad)


def issue_admin_token(username: str) -> str:
    settings = get_settings()
    payload = {"u": username, "exp": int(time.time()) + ADMIN_TOKEN_TTL}
    payload_b64 = _b64encode(json.dumps(payload).encode())
    sig = _sign(payload_b64, settings.ADMIN_PASSWORD)
    return f"{payload_b64}.{sig}"


def verify_admin_token(token: str) -> bool:
    settings = get_settings()
    try:
        payload_b64, sig = token.rsplit(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(_sign(payload_b64, settings.ADMIN_PASSWORD), sig):
        return False
    try:
        payload = json.loads(_b64decode(payload_b64))
    except Exception:
        return False
    return payload.get("exp", 0) > time.time()


async def require_admin(
    admin_session: str | None = Cookie(default=None, alias=ADMIN_COOKIE_NAME),
) -> None:
    if not admin_session or not verify_admin_token(admin_session):
        raise HTTPException(status_code=401, detail="需要管理員登入")
