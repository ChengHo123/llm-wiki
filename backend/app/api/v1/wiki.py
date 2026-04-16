import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.wiki_page import WikiPage
from app.models.wiki_link import WikiLink
from app.core.security import get_current_key
from app.services.lint import run_lint

router = APIRouter()


class WikiPageSummary(BaseModel):
    id: str
    title: str
    slug: str
    page_type: str
    updated_at: str


class WikiPageDetail(WikiPageSummary):
    content: str
    created_at: str


class GraphNode(BaseModel):
    id: str
    title: str
    slug: str
    page_type: str


class GraphEdge(BaseModel):
    source: str
    target: str
    link_text: str | None = None


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


@router.get("/wiki/pages", response_model=list[WikiPageSummary])
async def list_wiki_pages(
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WikiPage)
        .where(WikiPage.api_key_id == api_key.id)
        .order_by(WikiPage.updated_at.desc())
    )
    pages = result.scalars().all()
    return [
        WikiPageSummary(
            id=str(p.id),
            title=p.title,
            slug=p.slug,
            page_type=p.page_type,
            updated_at=p.updated_at.isoformat(),
        )
        for p in pages
    ]


@router.get("/wiki/pages/{page_id}", response_model=WikiPageDetail)
async def get_wiki_page(
    page_id: uuid.UUID,
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WikiPage).where(
            WikiPage.id == page_id,
            WikiPage.api_key_id == api_key.id,
        )
    )
    page = result.scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail="頁面不存在")
    return WikiPageDetail(
        id=str(page.id),
        title=page.title,
        slug=page.slug,
        page_type=page.page_type,
        content=page.content,
        created_at=page.created_at.isoformat(),
        updated_at=page.updated_at.isoformat(),
    )


@router.get("/wiki/graph", response_model=GraphResponse)
async def get_wiki_graph(
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    pages_result = await db.execute(
        select(WikiPage).where(WikiPage.api_key_id == api_key.id)
    )
    pages = pages_result.scalars().all()
    page_ids = {p.id for p in pages}

    links_result = await db.execute(
        select(WikiLink).where(WikiLink.source_page_id.in_(page_ids))
    )
    links = links_result.scalars().all()

    return GraphResponse(
        nodes=[
            GraphNode(id=str(p.id), title=p.title, slug=p.slug, page_type=p.page_type)
            for p in pages
        ],
        edges=[
            GraphEdge(source=str(l.source_page_id), target=str(l.target_page_id), link_text=l.link_text)
            for l in links
        ],
    )


@router.post("/wiki/lint")
async def lint_wiki(
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    """觸發 wiki 健檢，回傳問題報告"""
    return await run_lint(api_key.id, db)
