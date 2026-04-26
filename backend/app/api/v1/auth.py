from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.core.security import generate_api_key, hash_key, get_current_key
from app.services.login_pairing import create_code, poll

router = APIRouter()


class CreateKeyRequest(BaseModel):
    name: str


class CreateKeyResponse(BaseModel):
    key: str
    name: str
    message: str


class KeyInfo(BaseModel):
    name: str


class LinePairStartResponse(BaseModel):
    code: str
    expires_in: int


class LinePairPollResponse(BaseModel):
    status: str
    api_key: str | None = None
    name: str | None = None


@router.post("/keys", response_model=CreateKeyResponse)
async def create_api_key(body: CreateKeyRequest, db: AsyncSession = Depends(get_db)):
    """建立新 API Key（返回後無法再查詢，請妥善保存）"""
    raw_key = generate_api_key()
    api_key = ApiKey(key_hash=hash_key(raw_key), name=body.name)
    db.add(api_key)
    await db.commit()
    return CreateKeyResponse(
        key=raw_key,
        name=body.name,
        message="請保存此 API Key，之後將無法再次查看",
    )


@router.get("/keys/me", response_model=KeyInfo)
async def get_current_key_info(api_key: ApiKey = Depends(get_current_key)):
    """用當前 X-API-Key 取得 key 名稱（用於登入驗證）"""
    return KeyInfo(name=api_key.name)


@router.post("/auth/line-pair/start", response_model=LinePairStartResponse)
async def line_pair_start():
    """產生 6 位數配對碼。使用者到 LINE Bot 輸入 /login <code> 完成登入。"""
    return LinePairStartResponse(code=create_code(), expires_in=300)


@router.get("/auth/line-pair/poll", response_model=LinePairPollResponse)
async def line_pair_poll(code: str, db: AsyncSession = Depends(get_db)):
    """前端輪詢配對碼狀態。redeemed 時回傳 api_key 給前端儲存。"""
    status, api_key = poll(code)
    if status != "redeemed" or not api_key:
        return LinePairPollResponse(status=status)
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(api_key)))
    row = result.scalar_one_or_none()
    return LinePairPollResponse(
        status="redeemed",
        api_key=api_key,
        name=row.name if row else "LINE",
    )
