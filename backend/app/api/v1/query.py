from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.core.security import get_current_key
from app.services.query_service import run_query

router = APIRouter()


class QueryRequest(BaseModel):
    question: str
    save_to_wiki: bool = False


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
    )
    return QueryResponse(**result)
