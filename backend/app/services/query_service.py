import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func, text

from app.models.wiki_page import WikiPage
from app.models.wiki_link import WikiLink
from app.models.activity_log import ActivityLog
from app.services.llm import call_llm
from app.services.ingest import slugify

QUERY_SYSTEM_PROMPT = """你是一個個人知識庫助理。
以下是使用者的個人 wiki 頁面內容（以 XML 標籤分隔）。
請根據這些 wiki 內容回答使用者的問題。

規則：
- 優先根據 wiki 內容回答，若 wiki 沒有相關資訊請明確說明
- 回答使用 Markdown 格式
- 引用到的 wiki 頁面請用 [[頁面標題]] 標記
- 回答要清楚、有條理
"""


async def search_wiki_pages(
    query: str,
    api_key_id: uuid.UUID,
    db: AsyncSession,
    limit: int = 8,
) -> list[WikiPage]:
    """用 PostgreSQL 全文搜尋找相關 wiki 頁面"""
    # 先嘗試全文搜尋
    keywords = query.split()[:5]
    like_filters = [
        or_(
            WikiPage.title.ilike(f"%{kw}%"),
            WikiPage.content.ilike(f"%{kw}%"),
        )
        for kw in keywords
    ]

    result = await db.execute(
        select(WikiPage)
        .where(WikiPage.api_key_id == api_key_id, or_(*like_filters))
        .limit(limit)
    )
    return result.scalars().all()


async def run_query(
    question: str,
    api_key_id: uuid.UUID,
    db: AsyncSession,
    save_to_wiki: bool = False,
) -> dict:
    """執行查詢流程"""
    pages = await search_wiki_pages(question, api_key_id, db)

    if not pages:
        # fallback: 取最新的 10 頁
        result = await db.execute(
            select(WikiPage)
            .where(WikiPage.api_key_id == api_key_id)
            .order_by(WikiPage.updated_at.desc())
            .limit(10)
        )
        pages = result.scalars().all()

    # 建立 wiki context（供 prompt caching）
    wiki_context = "\n\n".join(
        f"<wiki_page title='{p.title}' slug='{p.slug}'>\n{p.content}\n</wiki_page>"
        for p in pages
    )

    system = f"{QUERY_SYSTEM_PROMPT}\n\n<wiki>\n{wiki_context}\n</wiki>"

    answer = await call_llm(
        system=system,
        messages=[{"role": "user", "content": question}],
        max_tokens=2048,
    )

    referenced_pages = [{"id": str(p.id), "title": p.title, "slug": p.slug} for p in pages]

    # 選擇性存回 wiki
    saved_page = None
    if save_to_wiki:
        slug = f"query-{slugify(question[:50])}"
        title = f"Q: {question[:100]}"
        content = f"## 問題\n{question}\n\n## 回答\n{answer}"

        existing = await db.execute(
            select(WikiPage).where(
                WikiPage.api_key_id == api_key_id,
                WikiPage.slug == slug,
            )
        )
        page = existing.scalar_one_or_none()
        if page:
            page.content = content
            page.updated_at = datetime.utcnow()
            # 清除舊連結，重新建立
            old_links = await db.execute(
                select(WikiLink).where(WikiLink.source_page_id == page.id)
            )
            for lnk in old_links.scalars():
                await db.delete(lnk)
        else:
            page = WikiPage(
                api_key_id=api_key_id,
                title=title,
                slug=slug,
                content=content,
                page_type="concept",
            )
            db.add(page)
        await db.flush()

        # 建立與被參考頁面的連結
        for ref_page in pages:
            if ref_page.id != page.id:
                db.add(WikiLink(
                    source_page_id=page.id,
                    target_page_id=ref_page.id,
                    link_text=f"參考：{ref_page.title}",
                ))

        saved_page = {"id": str(page.id), "title": title, "slug": slug}

    db.add(ActivityLog(
        api_key_id=api_key_id,
        action="query",
        details={"question": question, "pages_referenced": len(pages), "saved_to_wiki": save_to_wiki},
    ))
    await db.commit()

    return {
        "answer": answer,
        "referenced_pages": referenced_pages,
        "saved_page": saved_page,
    }
