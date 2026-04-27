"""In-memory pairing code store for LINE-initiated login.

Frontend asks for a 6-digit code; user types `/login <code>` to the LINE bot;
bot redeems the code with the LINE_BOT_WIKI_API_KEY; frontend polls and
receives the key. Codes expire after 5 minutes and are single-use.
"""
import secrets
import time
from dataclasses import dataclass
from typing import Literal

CODE_TTL_SECONDS = 300

PollStatus = Literal["pending", "redeemed", "expired"]


@dataclass
class _Entry:
    created_at: float
    redeemed: bool = False
    api_key: str | None = None


_store: dict[str, _Entry] = {}


def _purge_expired() -> None:
    now = time.time()
    expired = [c for c, e in _store.items() if now - e.created_at > CODE_TTL_SECONDS]
    for c in expired:
        del _store[c]


def create_code() -> str:
    _purge_expired()
    code = f"{secrets.randbelow(1_000_000):06d}"
    _store[code] = _Entry(created_at=time.time())
    return code


def redeem(code: str, api_key: str) -> bool:
    _purge_expired()
    entry = _store.get(code)
    if not entry or entry.redeemed:
        return False
    entry.redeemed = True
    entry.api_key = api_key
    return True


def poll(code: str) -> tuple[PollStatus, str | None]:
    _purge_expired()
    entry = _store.get(code)
    if not entry:
        return "expired", None
    if not entry.redeemed:
        return "pending", None
    api_key = entry.api_key
    del _store[code]
    return "redeemed", api_key
