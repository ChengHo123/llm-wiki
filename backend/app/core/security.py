import hashlib
import secrets
from datetime import datetime
from fastapi import Security, Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.line_user_binding import LineUserBinding
from app.models.web_session import WebSession
from app.core.end_user import current_end_user, line_tag, web_tag

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
session_token_header = APIKeyHeader(name="X-Session-Token", auto_error=False)


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> str:
    return f"wk_{secrets.token_urlsafe(32)}"


def generate_session_token() -> str:
    return f"ws_{secrets.token_urlsafe(48)}"


async def _set_end_user_for(api_key: ApiKey, db: AsyncSession) -> None:
    """查 line_user_bindings，把 contextvar 設成 line-{user_id} 或 web-{api_key_id}。"""
    binding = (
        await db.execute(
            select(LineUserBinding).where(LineUserBinding.api_key_id == api_key.id)
        )
    ).scalar_one_or_none()
    current_end_user.set(line_tag(binding.line_user_id) if binding else web_tag(api_key.id))


async def get_current_key(
    raw_key: str | None = Security(api_key_header),
    session_token: str | None = Security(session_token_header),
    db: AsyncSession = Depends(get_db),
) -> ApiKey:
    if raw_key:
        result = await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(raw_key)))
        api_key = result.scalar_one_or_none()
        if api_key:
            await _set_end_user_for(api_key, db)
            return api_key

    if session_token:
        result = await db.execute(
            select(WebSession).where(
                WebSession.session_token == session_token,
                WebSession.expires_at > datetime.utcnow(),
            )
        )
        session = result.scalar_one_or_none()
        if session:
            result = await db.execute(select(ApiKey).where(ApiKey.id == session.api_key_id))
            api_key = result.scalar_one_or_none()
            if api_key:
                await _set_end_user_for(api_key, db)
                return api_key

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key / session token",
    )
