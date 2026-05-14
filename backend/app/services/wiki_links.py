"""Wiki link reconciliation — code-side 兜底，保證 page.content 改完後 wiki_links 表跟著對齊。

任何會動到頁面 content 的流程（ingest / refine / lint apply）都應該呼叫
reconcile_wiki_links_from_content，確保圖譜、index 路由、orphan 統計拿到最新狀態。
"""
import re
import uuid

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wiki_page import WikiPage
from app.models.wiki_link import WikiLink


WIKILINK_PATTERN = re.compile(r"\[\[([^\]\|]+?)(?:\|[^\]]*)?\]\]")


def parse_wikilinks(content: str) -> set[str]:
    """從 markdown 內容抽出 [[target]] 或 [[target|顯示文字]] 中的 target 字串。"""
    if not content:
        return set()
    return {m.strip() for m in WIKILINK_PATTERN.findall(content) if m.strip()}


async def reconcile_wiki_links_from_content(
    db: AsyncSession,
    page: WikiPage,
    api_key_id: uuid.UUID,
) -> int:
    """重建 page 的 outgoing wiki_links，使其與 page.content 中的 [[...]] 一致。

    解析 [[target]]，把 target 當 slug 或 title 在同一 api_key 內查找，命中即建邊。
    指向不存在頁面的 wikilink 會被靜默忽略（不建懸空邊）。

    回傳實際建立的邊數量。呼叫端負責 commit。
    """
    targets = parse_wikilinks(page.content or "")

    await db.execute(delete(WikiLink).where(WikiLink.source_page_id == page.id))

    if not targets:
        return 0

    target_pages_result = await db.execute(
        select(WikiPage).where(
            WikiPage.api_key_id == api_key_id,
            (WikiPage.slug.in_(targets)) | (WikiPage.title.in_(targets)),
        )
    )
    candidates = target_pages_result.scalars().all()

    seen_ids: set[uuid.UUID] = set()
    for tp in candidates:
        if tp.id == page.id or tp.id in seen_ids:
            continue
        seen_ids.add(tp.id)
        db.add(WikiLink(source_page_id=page.id, target_page_id=tp.id))

    await db.flush()
    return len(seen_ids)
