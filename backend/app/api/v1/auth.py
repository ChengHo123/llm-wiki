from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.core.security import generate_api_key, hash_key, get_current_key

router = APIRouter()


class CreateKeyRequest(BaseModel):
    name: str


class CreateKeyResponse(BaseModel):
    key: str
    name: str
    message: str


class KeyInfo(BaseModel):
    name: str


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
