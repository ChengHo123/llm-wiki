from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.core.security import get_current_key
from app.services.query_service import run_query, run_query_stream

router = APIRouter()


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class QueryRequest(BaseModel):
    question: str
    save_to_wiki: bool = False  # 僅非串流端點使用
    history: list[HistoryMessage] = []


class QueryStreamRequest(BaseModel):
    question: str
    history: list[HistoryMessage] = []


class PageRef(BaseModel):
    id: str
    title: str
    slug: str


class QueryResponse(BaseModel):
    answer: str
    referenced_pages: list[PageRef]
    saved_page: PageRef | None = None


@router.post("/query", response_model=QueryResponse)
async def query_wiki(
    body: QueryRequest,
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    """向個人 wiki 提問，可選擇將回答存回 wiki"""
    result = await run_query(
        question=body.question,
        api_key_id=api_key.id,
        db=db,
        save_to_wiki=body.save_to_wiki,
        history=[h.model_dump() for h in body.history],
    )
    return QueryResponse(**result)


@router.post("/query/stream")
async def query_wiki_stream(
    body: QueryStreamRequest,
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    """串流版：以 NDJSON 回傳 pages / chunk / judge / done 事件。
    自動判斷是否存回 wiki，不接受手動 save_to_wiki 參數。"""
    return StreamingResponse(
        run_query_stream(
            question=body.question,
            api_key_id=api_key.id,
            db=db,
            history=[h.model_dump() for h in body.history],
        ),
        media_type="application/x-ndjson",
    )
