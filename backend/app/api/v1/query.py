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
    history: list[HistoryMessage] = []


class QueryStreamRequest(BaseModel):
    question: str
    history: list[HistoryMessage] = []


class PageRef(BaseModel):
    id: str
    title: str
    slug: str


class WikiEdit(BaseModel):
    action: Literal["update", "create"]
    slug: str
    title: str
    page_type: str
    reason: str


class WikiSaveInfo(BaseModel):
    save_decision: bool
    judge_reason: str
    applied_edits: list[WikiEdit] = []
    refine_summary: str = ""


class QueryResponse(BaseModel):
    answer: str
    referenced_pages: list[PageRef]
    saved_page: PageRef | None = None
    wiki_save: WikiSaveInfo | None = None


@router.post("/query", response_model=QueryResponse)
async def query_wiki(
    body: QueryRequest,
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    """向個人 wiki 提問（非串流）。
    Karpathy 模式：query 結果該複利回 wiki。是否實際存由系統 judge 把關，
    與 /query/stream 行為一致，不再因端點差異 silently 丟掉新知識。"""
    result = await run_query(
        question=body.question,
        api_key_id=api_key.id,
        db=db,
        save_to_wiki=True,
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
