import json
import re
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.document import Document
from app.models.wiki_page import WikiPage
from app.models.wiki_link import WikiLink
from app.models.activity_log import ActivityLog
from app.services.llm import call_llm, build_document_message
from app.core.config import get_settings

settings = get_settings()

INGEST_SYSTEM_PROMPT = """你是一個知識整理助手，負責將文件內容整合進個人 wiki 知識庫。

你的任務是分析輸入的文件，產生一組結構化的 wiki 頁面更新。

請以 JSON 格式回傳以下結構：
{
  "pages": [
    {
      "title": "頁面標題",
      "slug": "page-slug-in-kebab-case",
      "page_type": "summary|entity|concept|index",
      "content": "Markdown 格式的頁面內容，使用 [[頁面標題]] 語法建立內部連結",
      "links_to": ["連結到的其他頁面 slug 列表"]
    }
  ],
  "summary": "一段簡短的文件摘要"
}

規則：
- 盡量產生 10~15 個頁面
- page_type: summary=文件摘要, entity=人名/組織/產品, concept=概念/技術, index=索引頁
- 使用 [[標題]] 語法在 content 中標記頁面間的交叉連結
- slug 只使用英文小寫、數字、連字號
- content 使用 Markdown 格式，要有實質內容，不要只寫標題
"""


def slugify(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or f"page-{uuid.uuid4().hex[:8]}"


async def read_text_file(file_path: str) -> str:
    path = Path(file_path)
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return path.read_text(encoding="latin-1", errors="replace")


async def run_ingest(document_id: uuid.UUID, db: AsyncSession) -> None:
    """非同步執行 ingest 流程"""
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        return

    doc.status = "processing"
    await db.commit()

    try:
        suffix = Path(doc.file_path).suffix.lower()
        if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            msg = build_document_message(doc.file_path)
            # content 是 list，可以 append
            msg["content"].append({"type": "text", "text": f"文件名稱: {doc.filename}"})
        elif suffix == ".pdf":
            msg = build_document_message(doc.file_path)
            # PDF 經 pypdf 抽文字後 content 是 string，直接串接
            msg["content"] = f"文件名稱: {doc.filename}\n\n{msg['content']}"
        else:
            text = await read_text_file(doc.file_path)
            msg = {"role": "user", "content": f"文件名稱: {doc.filename}\n\n{text}"}

        raw = await call_llm(
            system=INGEST_SYSTEM_PROMPT,
            messages=[msg],
            max_tokens=8192,
        )

        # 解析 JSON（Claude 有時會包在 ```json ... ``` 裡）
        json_match = re.search(r"```json\s*([\s\S]+?)\s*```", raw)
        json_str = json_match.group(1) if json_match else raw
        data = json.loads(json_str)

        pages_created = []
        slug_to_id: dict[str, uuid.UUID] = {}

        for page_data in data.get("pages", []):
            title = page_data["title"]
            slug = page_data.get("slug") or slugify(title)

            # upsert: 找同一 api_key + slug 的頁面
            existing = await db.execute(
                select(WikiPage).where(
                    WikiPage.api_key_id == doc.api_key_id,
                    WikiPage.slug == slug,
                )
            )
            wiki_page = existing.scalar_one_or_none()

            if wiki_page:
                wiki_page.content = page_data.get("content", "")
                wiki_page.title = title
                wiki_page.page_type = page_data.get("page_type", "concept")
            else:
                wiki_page = WikiPage(
                    api_key_id=doc.api_key_id,
                    title=title,
                    slug=slug,
                    content=page_data.get("content", ""),
                    page_type=page_data.get("page_type", "concept"),
                )
                db.add(wiki_page)

            await db.flush()
            slug_to_id[slug] = wiki_page.id
            pages_created.append({"id": str(wiki_page.id), "title": title, "slug": slug})

        # 建立交叉連結
        for page_data in data.get("pages", []):
            source_slug = page_data.get("slug") or slugify(page_data["title"])
            source_id = slug_to_id.get(source_slug)
            if not source_id:
                continue

            # 清除舊連結
            old_links = await db.execute(
                select(WikiLink).where(WikiLink.source_page_id == source_id)
            )
            for link in old_links.scalars():
                await db.delete(link)

            for target_slug in page_data.get("links_to", []):
                target_id = slug_to_id.get(target_slug)
                if target_id and target_id != source_id:
                    db.add(WikiLink(source_page_id=source_id, target_page_id=target_id))

        doc.status = "done"
        db.add(ActivityLog(
            api_key_id=doc.api_key_id,
            action="ingest",
            details={
                "document_id": str(doc.id),
                "filename": doc.filename,
                "pages_created": len(pages_created),
                "summary": data.get("summary", ""),
            },
        ))
        await db.commit()

    except Exception as e:
        doc.status = "error"
        doc.error_message = str(e)
        await db.commit()
        raise
