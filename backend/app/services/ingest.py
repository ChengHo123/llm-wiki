import re
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.document import Document
from app.models.wiki_page import WikiPage
from app.models.wiki_link import WikiLink
from app.models.activity_log import ActivityLog
from app.services.llm import structured_call, vision_structured_call, build_document_message
from app.core.config import get_settings


class IngestPage(BaseModel):
    title: str = Field(description="頁面標題")
    slug: str = Field(description="slug 只使用英文小寫、數字、連字號")
    page_type: Literal["summary", "entity", "concept", "index"] = Field(
        description="summary=文件摘要, entity=人名/組織/產品, concept=概念/技術, index=索引頁"
    )
    content: str = Field(description="Markdown 格式內容，使用 [[標題]] 標記跨頁連結")
    links_to: list[str] = Field(default_factory=list, description="連結到的其他頁面 slug")


class IngestResult(BaseModel):
    pages: list[IngestPage] = Field(description="依內容產出的 wiki 頁面")
    summary: str = Field(description="一段簡短的文件摘要")


class OutlineSection(BaseModel):
    heading: str = Field(description="章節標題或主要段落名")
    brief: str = Field(description="該章節 1-2 句重點摘要")


class DocOutline(BaseModel):
    title: str = Field(description="整份文件的標題")
    summary: str = Field(description="整份文件的 3-5 句摘要")
    sections: list[OutlineSection] = Field(description="主要章節列表（依序）")
    main_entities: list[str] = Field(
        default_factory=list,
        description="全文提到的重要人物/組織/產品/地點",
    )
    main_concepts: list[str] = Field(
        default_factory=list,
        description="全文核心概念/技術/領域術語",
    )


# 長文件切塊參數
LARGE_DOC_CHAR_THRESHOLD = 50_000
CHUNK_SIZE = 40_000
CHUNK_OVERLAP = 4_000
OUTLINE_SAMPLE_HEAD = 80_000
OUTLINE_SAMPLE_TAIL = 40_000

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
- 頁數依文件內容量決定：薄文件 5~10 頁，厚文件 15~30 頁以上
- page_type: summary=文件摘要, entity=人名/組織/產品, concept=概念/技術, index=索引頁
- 使用 [[標題]] 語法在 content 中標記頁面間的交叉連結
- slug 只使用英文小寫、數字、連字號
- content 使用 Markdown 格式，要有實質內容，不要只寫標題
- `[[標題]]` 只可指向：(a) 本次 pages 列表內的 title，或 (b) <existing_wiki> 區塊內已存在的 slug/title。不准自創不存在的頁面名

【跨文件連結規則】
- 若提供 <existing_wiki> 區塊，代表知識庫已有頁面
- 新頁面的 links_to 可以連結到既有頁面的 slug（跨文件連結）
- 若新文件的概念與既有頁面重複，請使用既有頁面的 slug 與 title（會自動 upsert 更新內容）
- 不要重複建立已存在的實體/概念頁，優先補強既有頁面
"""


OUTLINE_SYSTEM_PROMPT = """你是文件結構分析助手。面對一份可能很長的文件，請先讀過整份（或抽樣），
產出這份文件的整體輪廓（outline）：標題、3~5 句摘要、主要章節列表、全文的主要實體與概念。

這份 outline 會當作後續 chunk-by-chunk 深度整理時的「全局靈魂」，所以：
- sections 要涵蓋全文主軸，依順序列出
- main_entities/main_concepts 要抓真正跨章節出現、值得獨立成 wiki 頁的項目
- summary 要描述文件整體目的、範圍、核心結論
- 不要產 wiki 頁面本身，這階段只做結構梳理
"""


CHUNK_INSTRUCTION = """你正在處理大型文件的其中一段（chunk）。請注意：
- <doc_outline> 是整份文件的全局輪廓，代表這份文件的「靈魂」，你每次只看到一段，但你產出的 wiki 頁要能融入整體
- <existing_wiki> 是前面 chunks 已產生的頁面。遇到同樣概念請 reuse 既有 slug（會自動 upsert 合併補強內容），不要另起爐灶
- 本 chunk 結尾可能切在段落中間，不完整的段落請忽略或略過，專注在能獨立成立的內容
- 本 chunk 開頭可能有上一 chunk 重疊的文字，不用重複整理
- 本次只需為此 chunk 的主題產頁面，不用硬生出 10~15 頁
"""


def build_existing_wiki_context(pages: list) -> str:
    """把現有 wiki 頁面整理成 index context 供 LLM 參考"""
    if not pages:
        return ""
    lines = ["<existing_wiki>"]
    for p in pages:
        summary = (p.content or "").strip().replace("\n", " ")[:120]
        lines.append(f"- slug: {p.slug} | title: {p.title} | type: {p.page_type} | summary: {summary}")
    lines.append("</existing_wiki>")
    return "\n".join(lines)


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


def extract_pdf_text(file_path: str) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    except Exception:
        return ""


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """固定字元切塊，相鄰 chunk 保留 overlap 避免段落被切斷。"""
    if len(text) <= size:
        return [text]
    chunks = []
    i = 0
    while i < len(text):
        end = min(i + size, len(text))
        chunks.append(text[i:end])
        if end == len(text):
            break
        i = end - overlap
    return chunks


def outline_to_context(o: DocOutline) -> str:
    lines = ["<doc_outline>", f"標題: {o.title}", f"摘要: {o.summary}", "章節:"]
    for s in o.sections:
        lines.append(f"  - {s.heading}: {s.brief}")
    if o.main_entities:
        lines.append("主要實體: " + ", ".join(o.main_entities))
    if o.main_concepts:
        lines.append("主要概念: " + ", ".join(o.main_concepts))
    lines.append("</doc_outline>")
    return "\n".join(lines)


async def generate_outline(full_text: str) -> DocOutline:
    """用整份文件（或抽樣）產 outline。樣本策略：超長則取頭 + 尾。"""
    if len(full_text) <= OUTLINE_SAMPLE_HEAD + OUTLINE_SAMPLE_TAIL:
        sample = full_text
    else:
        sample = (
            full_text[:OUTLINE_SAMPLE_HEAD]
            + "\n\n...（中段省略）...\n\n"
            + full_text[-OUTLINE_SAMPLE_TAIL:]
        )
    return await structured_call(
        schema=DocOutline,
        system=OUTLINE_SYSTEM_PROMPT,
        user=sample,
        max_tokens=8192,
    )


async def _apply_ingest_result(
    db: AsyncSession,
    doc_id: uuid.UUID,
    api_key_id: uuid.UUID,
    data: dict,
) -> list[dict]:
    """把一次 structured_call 的結果 upsert 到 DB（頁面 + 連結）。
    回傳本次新增/更新的頁面摘要列表。必須在外層 commit。
    """
    existing_result = await db.execute(
        select(WikiPage).where(WikiPage.api_key_id == api_key_id)
    )
    existing_pages = existing_result.scalars().all()
    existing_slugs: dict[str, uuid.UUID] = {p.slug: p.id for p in existing_pages}

    pages_created = []
    slug_to_id: dict[str, uuid.UUID] = {}

    for page_data in data.get("pages", []):
        title = page_data["title"]
        slug = page_data.get("slug") or slugify(title)

        existing = await db.execute(
            select(WikiPage).where(
                WikiPage.api_key_id == api_key_id,
                WikiPage.slug == slug,
            )
        )
        wiki_page = existing.scalar_one_or_none()

        if wiki_page:
            wiki_page.content = page_data.get("content", "")
            wiki_page.title = title
            wiki_page.page_type = page_data.get("page_type", "concept")
            if wiki_page.source_document_id is None:
                wiki_page.source_document_id = doc_id
        else:
            wiki_page = WikiPage(
                api_key_id=api_key_id,
                source_document_id=doc_id,
                title=title,
                slug=slug,
                content=page_data.get("content", ""),
                page_type=page_data.get("page_type", "concept"),
            )
            db.add(wiki_page)

        await db.flush()
        slug_to_id[slug] = wiki_page.id
        pages_created.append({"id": str(wiki_page.id), "title": title, "slug": slug})

    slug_to_id_all = {**existing_slugs, **slug_to_id}

    for page_data in data.get("pages", []):
        source_slug = page_data.get("slug") or slugify(page_data["title"])
        source_id = slug_to_id.get(source_slug)
        if not source_id:
            continue

        old_links = await db.execute(
            select(WikiLink).where(WikiLink.source_page_id == source_id)
        )
        for link in old_links.scalars():
            await db.delete(link)

        for target_slug in page_data.get("links_to", []):
            target_id = slug_to_id_all.get(target_slug)
            if target_id and target_id != source_id:
                db.add(WikiLink(source_page_id=source_id, target_page_id=target_id))

    return pages_created


async def run_ingest(document_id: uuid.UUID) -> None:
    """非同步執行 ingest 流程（自行建立 DB session，避免與 request scope 衝突）。

    長文件走 chunked 路徑：
      1. 抽全文 → 產 outline（全局靈魂）
      2. 切成 overlap chunks
      3. 每 chunk 帶 outline + existing_wiki ingest，逐塊 commit
    短文件走原本單次 path。
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()
        if not doc:
            return

        await db.commit()

        try:
            suffix = Path(doc.file_path).suffix.lower()
            is_image = suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp")

            # 抽全文（PDF / 文字檔才有）；圖片走原本的 multimodal 單次 path
            full_text = ""
            if suffix == ".pdf":
                full_text = extract_pdf_text(doc.file_path)
            elif not is_image:
                full_text = await read_text_file(doc.file_path)

            use_chunked = (not is_image) and len(full_text) > LARGE_DOC_CHAR_THRESHOLD

            all_pages_created: list[dict] = []
            doc_summary = ""

            if use_chunked:
                # 1. outline
                outline = await generate_outline(full_text)
                outline_ctx = outline_to_context(outline)
                doc_summary = outline.summary

                # 2. chunks
                chunks = chunk_text(full_text)
                chunked_system = f"{INGEST_SYSTEM_PROMPT}\n\n{CHUNK_INSTRUCTION}"

                for idx, chunk in enumerate(chunks):
                    existing_result = await db.execute(
                        select(WikiPage).where(WikiPage.api_key_id == doc.api_key_id)
                    )
                    existing_pages = existing_result.scalars().all()
                    existing_context = build_existing_wiki_context(existing_pages)

                    parts = [outline_ctx]
                    if existing_context:
                        parts.append(existing_context)
                    parts.append(
                        f"文件名稱: {doc.filename}（第 {idx + 1}/{len(chunks)} 段）\n\n{chunk}"
                    )
                    user_content = "\n\n".join(parts)

                    chunk_result: IngestResult = await structured_call(
                        schema=IngestResult,
                        system=chunked_system,
                        user=user_content,
                        max_tokens=16384,
                    )
                    chunk_data = chunk_result.model_dump()
                    created = await _apply_ingest_result(
                        db, doc.id, doc.api_key_id, chunk_data,
                    )
                    all_pages_created.extend(created)
                    await db.commit()
            else:
                # 原本單次 path
                existing_result = await db.execute(
                    select(WikiPage).where(WikiPage.api_key_id == doc.api_key_id)
                )
                existing_pages = existing_result.scalars().all()
                existing_context = build_existing_wiki_context(existing_pages)

                if is_image:
                    msg = build_document_message(doc.file_path)
                    msg["content"].append({"type": "text", "text": f"文件名稱: {doc.filename}"})
                    if existing_context:
                        msg["content"].insert(0, {"type": "text", "text": existing_context})
                    user_content = msg["content"]
                    single_result: IngestResult = await vision_structured_call(
                        schema=IngestResult,
                        system=INGEST_SYSTEM_PROMPT,
                        user_content=user_content,
                        max_tokens=16384,
                    )
                else:
                    prefix = f"{existing_context}\n\n" if existing_context else ""
                    user_content = f"{prefix}文件名稱: {doc.filename}\n\n{full_text}"
                    single_result = await structured_call(
                        schema=IngestResult,
                        system=INGEST_SYSTEM_PROMPT,
                        user=user_content,
                        max_tokens=32768,
                    )
                data = single_result.model_dump()
                doc_summary = data.get("summary", "")
                all_pages_created = await _apply_ingest_result(
                    db, doc.id, doc.api_key_id, data,
                )

            doc.status = "done"
            db.add(ActivityLog(
                api_key_id=doc.api_key_id,
                action="ingest",
                details={
                    "document_id": str(doc.id),
                    "filename": doc.filename,
                    "pages_created": len(all_pages_created),
                    "summary": doc_summary,
                    "chunked": use_chunked,
                },
            ))
            await db.commit()

        except Exception as e:
            doc.status = "error"
            doc.error_message = str(e)
            await db.commit()
            raise
