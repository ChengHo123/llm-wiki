import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func

from app.db.session import AsyncSessionLocal
from app.models.document import Document
from app.models.wiki_page import WikiPage
from app.models.wiki_page_source import WikiPageSource
from app.models.wiki_link import WikiLink
from app.models.activity_log import ActivityLog
from app.services.llm import structured_call, vision_extract_text
from app.core.config import get_settings
from app.core.end_user import current_end_user, line_tag, web_tag
from app.models.line_user_binding import LineUserBinding


class IngestPage(BaseModel):
    title: str = Field(description="頁面標題")
    slug: str = Field(description="slug 只使用英文小寫、數字、連字號")
    page_type: Literal["summary", "entity", "concept", "index"] = Field(
        description="summary=文件摘要, entity=人名/組織/產品, concept=概念/技術, index=索引頁"
    )
    summary: str = Field(
        default="",
        description="1-2 句、最多 150 字的本頁主題濃縮，用於 wiki 索引；不可只重述 title",
    )
    content: str = Field(description="Markdown 格式內容，使用 [[標題]] 標記跨頁連結")
    links_to: list[str] = Field(default_factory=list, description="連結到的其他頁面 slug")


class BackLinkEdit(BaseModel):
    target_slug: str = Field(description="既有頁面的 slug，必須存在於 <existing_pages> 中")
    new_content: str = Field(
        description="整合連結後的完整新內容（保留原頁精髓 + 自然嵌入 [[新頁標題]]）"
    )
    new_summary: str = Field(
        default="",
        description="改寫後的 1-2 句、最多 150 字主題摘要；應反映新增的關聯",
    )
    reason: str = Field(description="為何補這個連結")


class BackLinkPlan(BaseModel):
    updates: list[BackLinkEdit] = Field(
        default_factory=list,
        description="對既有頁的回寫編輯；若沒有舊頁需要補連結，回傳空列表",
    )
    summary: str = Field(description="此 plan 的整體說明")


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
# Outline map-reduce：每段 mini-outline 看到的字元數；超過 OUTLINE_CHUNK_SIZE*1.2 才走 map-reduce
OUTLINE_CHUNK_SIZE = 100_000

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
      "summary": "1-2 句、最多 150 字的主題濃縮，供 wiki 索引/路由使用",
      "content": "Markdown 格式的頁面內容，使用 [[頁面標題]] 語法建立內部連結",
      "links_to": ["連結到的其他頁面 slug 列表"]
    }
  ],
  "summary": "一段簡短的文件摘要"
}

產出規則：
- 頁數依內容量決定：薄文件 5~10 頁、厚文件 15~30 頁
- 每份文件最多 1~2 個 summary 頁；其餘是 entity（人/組織/產品）或 concept（概念/技術/方法）
- 一頁一主題：A 和 B 是不同實體就拆 2 頁，再用 [[B]] 互連
- **index 頁是 query 路由的入口**：同類 entity/concept ≥3 個時主動建一個 index 頁
  - title 像「XX 領域」「XX 系列人物」這種能代表一群頁面的主題詞
  - content 是條列式 [[標題]] 清單，分類列出該主題下所有頁面
  - links_to 必須涵蓋所有被列出的頁面
  - 沒有 index 頁，query 時只能逐頁搜尋，整個 wiki 路由會崩潰

content 欄位（重點）：
- **本頁是 wiki 的肉**，要寫成可獨立閱讀的完整段落：原文細節、引用、上下文都要保留
- 不准只放標題、只寫一句話、只列分類
- 每頁 200~500 字實質內容是常態。短於此通常代表偷工
- content 內用 [[標題]] 標記交叉連結

summary 欄位：
- 1-2 句、最多 150 字
- 必須包含本頁的關鍵實體/概念與相鄰主題的關係，不可重述 title
- 這是搜尋進入點，寫不好整頁會被漏掉

links_to 欄位：
- 只要和其他頁有任何明確關聯就一定要連，沒 link 等於 query 時的孤兒
- 只可指向：本次 pages 列表內的 title，或 <existing_wiki> 區塊已存在的 slug/title
- 不准自創不存在的頁面名

slug 與既有 wiki：
- slug 只用英文小寫、數字、連字號
- 若 <existing_wiki> 已有相同主題，重用既有 slug 與 title（會自動 upsert 合併），不要另起爐灶

絕對禁止：
- 不要在 content / summary / title 提到資料的處理過程或所用技術。包括但不限於：「OCR」「視覺模型」「圖片轉文字」「LLM 抽取」「PDF 解析」「來自掃描檔」「根據附圖」等
- wiki 內容只描述「事物本身」，不描述「資料怎麼來」。當作 source 就是純文字原文寫的
"""


OUTLINE_SYSTEM_PROMPT = """你是文件結構分析助手。面對一份可能很長的文件，請先讀過整份（或抽樣），
產出這份文件的整體輪廓（outline）：標題、3~5 句摘要、主要章節列表、全文的主要實體與概念。

這份 outline 會當作後續 chunk-by-chunk 深度整理時的「全局靈魂」，所以：
- sections 要涵蓋全文主軸，依順序列出
- main_entities/main_concepts 要抓真正跨章節出現、值得獨立成 wiki 頁的項目
- summary 要描述文件整體目的、範圍、核心結論
- 不要產 wiki 頁面本身，這階段只做結構梳理
"""


# Map 階段：對長文件的某一段做局部 outline。會 append 在 OUTLINE_SYSTEM_PROMPT 之後。
OUTLINE_MINI_INSTRUCTION = """【注意】你看到的不是整份文件，而是其中一段（第 {idx}/{total} 段）。
請只針對這段做局部 outline：
- title: 暫定（看到什麼線索就寫什麼，後續會被合併覆蓋）
- summary: 這段的內容摘要
- sections: 這段內出現的章節，依順序
- main_entities/main_concepts: 這段內出現的重要實體與概念
寧可多列也不要漏，後續 reduce 階段會去重合併。"""


# Reduce 階段：把多段 mini-outline 合成整份文件的 final outline
OUTLINE_MERGE_SYSTEM_PROMPT = """你正在彙整一份長文件多段局部 outline，請合併成整份文件的最終 outline。

合併規則：
- title: 從各段 title 中挑最能代表整份文件的，或合成新標題
- summary: 用 3-5 句涵蓋全文主軸（開始 → 中段 → 結尾），不能只描述某一段
- sections: 依文件原始順序串接所有段落的 sections，去除明顯重複，但寧可保留也不刪除
- main_entities: 集合各段實體，去重，依重要程度排序
- main_concepts: 同上

絕對不要漏掉任何一段提到的重要章節/實體/概念。寧可多保留，這是整份文件的靈魂，後續 chunk-by-chunk ingest 都會帶這份 outline。"""


CHUNK_INSTRUCTION = """你正在處理大型文件的其中一段（chunk）。請注意：
- <doc_outline> 是整份文件的全局輪廓，每塊只看到一段，但產出的 wiki 頁要能融入整體
- <existing_wiki> 是前面 chunks 已產生的頁面。遇到同樣概念請 reuse 既有 slug（會自動 upsert 合併），不要另起爐灶
- chunk 結尾可能切在段落中間，不完整的段落請忽略
- chunk 開頭可能和上一 chunk 重疊，不要重複整理
- 本 chunk 該產幾頁依本段內容決定；該段內容多就多產、少就少產，content 字數別因為「只是一塊」就縮水
"""


BACK_LINK_SYSTEM_PROMPT = """你是個人 wiki 的策展人。剛有一批新頁面被加入 wiki，請掃描既有頁面，找出哪些**舊頁面語意上應該補連結**指向新頁面。

## 這個 wiki 的靈魂
- wiki 是「蒸餾後的結構化知識」，每頁靠 [[wikilink]] 互相連結，追求知識複利累積
- 新文件帶入了新概念/實體，舊頁可能在語意上應該連結到這些新概念，但目前還沒有引用
- 你的任務：發現這些遺漏的 cross-reference，自然地補進舊頁面內容裡

## 三種值得回寫的情況（任一滿足）
1. 舊頁提到了新頁面對應的人物/組織/概念，但沒有用 [[]] 標記
2. 新頁面對舊頁面的某個段落是合理延伸 / 細節補充 / 反例 / 對比
3. 兩頁屬同一語意網絡（同主題/同領域/上下位概念），值得交叉指涉

## 嚴格規則
- 只更新真正能語意連結的舊頁面，不要為了更新而更新
- 保留舊頁原本內容精髓與結構，**只在語意相關的位置自然嵌入** [[新頁標題]]
- new_content 必須是該舊頁**完整的新版本**（含原內容 + 嵌入的連結）
- 不要重寫整個頁面、不要改變主題、不要刪除原資訊
- 不要連結語意不相關的頁面（寧缺勿濫）
- target_slug 必須是 <existing_pages> 中的 slug，不准自創
- 每個 update 都要產生 `new_summary`（1-2 句、最多 150 字），反映加入新關聯後的主題摘要
- 若沒有舊頁適合補連結，updates 回傳空列表

## 輸入
- <new_pages>：本次新加入或更新的頁面（含完整 content）
- <existing_pages>：所有其他既有頁面（含完整 content）

## 輸出
BackLinkPlan 包含 updates list 與 summary。每個 update 給 target_slug + new_content + reason。
"""


async def back_link_pass(
    db: AsyncSession,
    api_key_id: uuid.UUID,
    new_page_slugs: set[str],
) -> list[dict]:
    """ingest 完成後回寫舊頁。讓 LLM 掃描既有頁找出應補 cross-reference 的，批次更新。"""
    all_pages_result = await db.execute(
        select(WikiPage).where(WikiPage.api_key_id == api_key_id)
    )
    all_pages = all_pages_result.scalars().all()

    new_pages = [p for p in all_pages if p.slug in new_page_slugs]
    old_pages = [p for p in all_pages if p.slug not in new_page_slugs]

    if not new_pages or not old_pages:
        return []

    # 套用 two-phase context budget，避免大 wiki 把 prompt 爆掉
    from app.services.query_service import degrade_page_bodies

    new_bodies = degrade_page_bodies(new_pages, max_chars=20000)
    old_bodies = degrade_page_bodies(old_pages, max_chars=40000)
    new_ctx = "\n\n".join(
        f"<page slug=\"{p.slug}\" title=\"{p.title}\" type=\"{p.page_type}\">\n{body}\n</page>"
        for p, body in zip(new_pages, new_bodies)
    )
    old_ctx = "\n\n".join(
        f"<page slug=\"{p.slug}\" title=\"{p.title}\" type=\"{p.page_type}\">\n{body}\n</page>"
        for p, body in zip(old_pages, old_bodies)
    )
    user_msg = (
        f"<new_pages>\n{new_ctx}\n</new_pages>\n\n"
        f"<existing_pages>\n{old_ctx}\n</existing_pages>"
    )

    plan = await structured_call(
        schema=BackLinkPlan,
        system=BACK_LINK_SYSTEM_PROMPT,
        user=user_msg,
        max_tokens=32768,
    )

    old_by_slug = {p.slug: p for p in old_pages}
    applied: list[dict] = []
    for upd in plan.updates:
        page = old_by_slug.get(upd.target_slug)
        if not page:
            continue  # LLM 幻覺了不存在的 slug
        page.content = upd.new_content
        if upd.new_summary:
            page.summary = upd.new_summary
        page.updated_at = datetime.utcnow()
        await db.flush()
        applied.append({
            "target_slug": upd.target_slug,
            "title": page.title,
            "reason": upd.reason,
        })
    if applied:
        await db.commit()
    return applied


async def synthesize_doc_index_page(
    db: AsyncSession,
    doc: Document,
    pages_created: list[dict],
    doc_summary: str,
) -> dict | None:
    """為這份文件 upsert 一個 doc-level 索引頁。code-side 合成，不依賴 LLM。

    query 路由用 page_type='index' 當錨點。LLM prompt 雖鼓勵建 index，但實際常忽略，
    因此這裡兜底保證每份「貢獻 ≥3 個主題頁」的文件都有 1 個索引頁可被 query 命中。
    LLM 自己生的 topic-cluster index 仍會保留（slug 不衝突），兩種共存。
    """
    if len(pages_created) < 3:
        return None

    created_slugs = [p["slug"] for p in pages_created]
    pages_result = await db.execute(
        select(WikiPage).where(
            WikiPage.api_key_id == doc.api_key_id,
            WikiPage.slug.in_(created_slugs),
        )
    )
    pages = pages_result.scalars().all()
    topic_pages = [p for p in pages if p.page_type != "index"]
    if len(topic_pages) < 3:
        return None

    doc_stem = Path(doc.filename).stem if doc.filename else "doc"
    base_slug = slugify(doc_stem)[:40] or "doc"
    index_slug = f"doc-index-{base_slug}-{str(doc.id)[:8]}"
    index_title = f"{doc_stem} 索引"

    type_label = {"entity": "實體", "concept": "概念", "summary": "摘要"}
    by_type: dict[str, list[WikiPage]] = {}
    for p in topic_pages:
        by_type.setdefault(p.page_type, []).append(p)

    content_lines = [f"本頁是文件「{doc_stem}」的索引，列出此文件貢獻的所有主題頁。", ""]
    for ptype in ("summary", "entity", "concept"):
        ps = by_type.get(ptype)
        if not ps:
            continue
        content_lines.append(f"## {type_label.get(ptype, ptype)}")
        for p in sorted(ps, key=lambda x: x.title):
            content_lines.append(f"- [[{p.title}]]")
        content_lines.append("")
    for ptype, ps in by_type.items():
        if ptype in ("summary", "entity", "concept"):
            continue
        content_lines.append(f"## {type_label.get(ptype, ptype)}")
        for p in sorted(ps, key=lambda x: x.title):
            content_lines.append(f"- [[{p.title}]]")
        content_lines.append("")
    content = "\n".join(content_lines).strip()

    summary = (doc_summary or "").strip().replace("\n", " ")[:200]
    if not summary:
        topics = "、".join(p.title for p in topic_pages[:5])
        summary = f"{doc_stem} 文件索引：涵蓋 {topics} 等 {len(topic_pages)} 個主題"

    existing = await db.execute(
        select(WikiPage).where(
            WikiPage.api_key_id == doc.api_key_id,
            WikiPage.slug == index_slug,
        )
    )
    index_page = existing.scalar_one_or_none()
    if index_page:
        index_page.title = index_title
        index_page.content = content
        index_page.summary = summary
        index_page.page_type = "index"
        index_page.updated_at = datetime.utcnow()
        if index_page.source_document_id is None:
            index_page.source_document_id = doc.id
    else:
        index_page = WikiPage(
            api_key_id=doc.api_key_id,
            source_document_id=doc.id,
            title=index_title,
            slug=index_slug,
            content=content,
            page_type="index",
            summary=summary,
        )
        db.add(index_page)
    await db.flush()

    src_exists = await db.execute(
        select(WikiPageSource).where(
            WikiPageSource.wiki_page_id == index_page.id,
            WikiPageSource.document_id == doc.id,
        )
    )
    if src_exists.scalar_one_or_none() is None:
        db.add(WikiPageSource(wiki_page_id=index_page.id, document_id=doc.id))
        await db.flush()

    old_links = await db.execute(
        select(WikiLink).where(WikiLink.source_page_id == index_page.id)
    )
    for link in old_links.scalars():
        await db.delete(link)
    for tp in topic_pages:
        db.add(WikiLink(source_page_id=index_page.id, target_page_id=tp.id))
    await db.flush()

    return {"id": str(index_page.id), "title": index_title, "slug": index_slug}


def build_existing_wiki_context(pages: list) -> str:
    """把現有 wiki 頁面整理成 index context 供 LLM 參考。優先用 wiki_page.summary，沒有才退用 content。"""
    if not pages:
        return ""
    lines = ["<existing_wiki>"]
    for p in pages:
        s = (p.summary or "").strip()
        if not s:
            s = (p.content or "").strip().replace("\n", " ")[:120]
        else:
            s = s.replace("\n", " ")[:200]
        lines.append(f"- slug: {p.slug} | title: {p.title} | type: {p.page_type} | summary: {s}")
    lines.append("</existing_wiki>")
    return "\n".join(lines)


def normalize_markdown(text: str) -> str:
    """修補 LLM 常見 markdown 格式 bug：把 inline 的 `## `、`* `、`1. ` 等區塊符號前面補換行，
    避免 ReactMarkdown 把整段當純文字呈現。

    策略：
    - heading `## ` 直接補換行（## 不會在 inline 用法出現）
    - bullet / number list：line-by-line 偵測，若一行含 ≥2 個 `* ` 或 `1. ` 之類的 pattern，
      視為「LLM 把整段 list 擠成一行」，逐個拆分。單一 `* ` 不拆，避免破壞 *italic*。
    """
    if not text:
        return text
    # 1) headings：## / ### / #### 前若沒換行就補
    text = re.sub(r"(?<=[^\n])\s*(#{1,6}\s)", r"\n\n\1", text)

    out_lines = []
    bullet_pattern = re.compile(r"(?:(?<=^)|(?<=[^\n*]))\*\s+")
    number_pattern = re.compile(r"(?:(?<=^)|(?<=[^\n]))\d+\.\s+")
    heading_line = re.compile(r"^#{1,6}\s")
    for line in text.split("\n"):
        bullet_hits = len(bullet_pattern.findall(line))
        number_hits = len(number_pattern.findall(line))
        is_heading = bool(heading_line.match(line))
        # heading 那行內若混入 `* ` 一律拆；其他行需要 ≥2 個 `* ` 才視為 list
        if is_heading and bullet_hits >= 1:
            line = re.sub(r"(?<=[^\n*])\s*(\*\s+)", r"\n\1", line)
        elif bullet_hits >= 2:
            line = re.sub(r"(?<=[^\n*])\s*(\*\s+)", r"\n\1", line)
        if number_hits >= 2:
            line = re.sub(r"(?<=[^\n])\s+(\d+\.\s)", r"\n\1", line)
        out_lines.append(line)
    text = "\n".join(out_lines)

    # 3) 連續 3 個以上換行縮成 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def infer_intra_doc_links(pages: list[dict], slug_set: set[str]) -> dict[str, set[str]]:
    """LLM 經常忘記填 links_to。掃描每頁 content，找出本批其他頁面的 title / slug 出現過的地方，
    自動補成連結。回傳 {source_slug: {target_slug, ...}}。
    """
    title_to_slug: dict[str, str] = {}
    for p in pages:
        t = (p.get("title") or "").strip()
        s = (p.get("slug") or slugify(t)).strip()
        if t and s:
            title_to_slug[t] = s

    inferred: dict[str, set[str]] = {}
    for p in pages:
        src_slug = (p.get("slug") or slugify(p.get("title") or "")).strip()
        if not src_slug:
            continue
        hay = (p.get("content") or "") + "\n" + (p.get("summary") or "")
        targets = set()
        for title, slug in title_to_slug.items():
            if slug == src_slug:
                continue
            if slug not in slug_set:
                continue
            # 完整 title 出現在內容裡，且不只是字串中的單一字
            if len(title) >= 2 and title in hay:
                targets.add(slug)
        if targets:
            inferred[src_slug] = targets
    return inferred


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
    """pypdf 抽純文字。掃描型/圖片型 PDF 會抽出極少文字，呼叫端可依長度判斷品質。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        import logging
        logging.getLogger(__name__).info(
            "extract_pdf_text: %s → %d chars, %d pages",
            file_path, len(text), len(reader.pages),
        )
        return text
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("extract_pdf_text failed: %s", e)
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


async def _mini_outline(chunk: str, idx: int, total: int) -> DocOutline:
    """Map 階段：對長文件的一段做局部 outline。"""
    system = (
        f"{OUTLINE_SYSTEM_PROMPT}\n\n"
        f"{OUTLINE_MINI_INSTRUCTION.format(idx=idx + 1, total=total)}"
    )
    return await structured_call(
        schema=DocOutline,
        system=system,
        user=chunk,
        max_tokens=4096,
    )


def _format_mini_outline(o: DocOutline, idx: int, total: int) -> str:
    lines = [f"<part {idx + 1}/{total}>", f"標題: {o.title}", f"摘要: {o.summary}"]
    if o.sections:
        lines.append("章節:")
        for s in o.sections:
            lines.append(f"  - {s.heading}: {s.brief}")
    if o.main_entities:
        lines.append("實體: " + ", ".join(o.main_entities))
    if o.main_concepts:
        lines.append("概念: " + ", ".join(o.main_concepts))
    lines.append(f"</part {idx + 1}/{total}>")
    return "\n".join(lines)


async def _merge_outlines(minis: list[DocOutline]) -> DocOutline:
    """Reduce 階段：把多段 mini-outline 合併成整份文件的 final outline。"""
    merged_input = "\n\n".join(
        _format_mini_outline(o, i, len(minis)) for i, o in enumerate(minis)
    )
    return await structured_call(
        schema=DocOutline,
        system=OUTLINE_MERGE_SYSTEM_PROMPT,
        user=merged_input,
        max_tokens=8192,
    )


async def generate_outline(full_text: str) -> DocOutline:
    """長文件 outline：超過 OUTLINE_CHUNK_SIZE*1.2 走 map-reduce，全文都會被看到，不丟中段。"""
    threshold = int(OUTLINE_CHUNK_SIZE * 1.2)
    if len(full_text) <= threshold:
        return await structured_call(
            schema=DocOutline,
            system=OUTLINE_SYSTEM_PROMPT,
            user=full_text,
            max_tokens=8192,
        )

    chunks: list[str] = []
    i = 0
    while i < len(full_text):
        chunks.append(full_text[i : i + OUTLINE_CHUNK_SIZE])
        i += OUTLINE_CHUNK_SIZE

    minis: list[DocOutline] = []
    for idx, chunk in enumerate(chunks):
        minis.append(await _mini_outline(chunk, idx, len(chunks)))

    return await _merge_outlines(minis)


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

        normalized_content = normalize_markdown(page_data.get("content", ""))

        if wiki_page:
            wiki_page.content = normalized_content
            wiki_page.title = title
            wiki_page.page_type = page_data.get("page_type", "concept")
            wiki_page.summary = page_data.get("summary", "") or wiki_page.summary
            if wiki_page.source_document_id is None:
                wiki_page.source_document_id = doc_id
        else:
            wiki_page = WikiPage(
                api_key_id=api_key_id,
                source_document_id=doc_id,
                title=title,
                slug=slug,
                content=normalized_content,
                page_type=page_data.get("page_type", "concept"),
                summary=page_data.get("summary", ""),
            )
            db.add(wiki_page)

        await db.flush()
        slug_to_id[slug] = wiki_page.id
        pages_created.append({"id": str(wiki_page.id), "title": title, "slug": slug})

        # 記錄這份文件是這頁的 source（多對多）；同一 (page, doc) 已存在則跳過
        existing_link = await db.execute(
            select(WikiPageSource).where(
                WikiPageSource.wiki_page_id == wiki_page.id,
                WikiPageSource.document_id == doc_id,
            )
        )
        if existing_link.scalar_one_or_none() is None:
            db.add(WikiPageSource(wiki_page_id=wiki_page.id, document_id=doc_id))
            await db.flush()

    slug_to_id_all = {**existing_slugs, **slug_to_id}

    # 強制保底：LLM 經常忘填 links_to，掃 content 推論本批內互相提及的連結，
    # 再 union 進 LLM 給的 links_to。
    pages_list = data.get("pages", []) or []
    inferred_links = infer_intra_doc_links(pages_list, set(slug_to_id.keys()))

    for page_data in pages_list:
        source_slug = page_data.get("slug") or slugify(page_data["title"])
        source_id = slug_to_id.get(source_slug)
        if not source_id:
            continue

        old_links = await db.execute(
            select(WikiLink).where(WikiLink.source_page_id == source_id)
        )
        for link in old_links.scalars():
            await db.delete(link)

        target_slugs = set(page_data.get("links_to", []) or [])
        target_slugs |= inferred_links.get(source_slug, set())

        seen_targets: set = set()
        for target_slug in target_slugs:
            target_id = slug_to_id_all.get(target_slug)
            if target_id and target_id != source_id and target_id not in seen_targets:
                db.add(WikiLink(source_page_id=source_id, target_page_id=target_id))
                seen_targets.add(target_id)

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

        # 設 LiteLLM end-user 標籤：LINE 用 line-{user_id}，否則 web-{api_key_id}
        binding = (
            await db.execute(
                select(LineUserBinding).where(LineUserBinding.api_key_id == doc.api_key_id)
            )
        ).scalar_one_or_none()
        current_end_user.set(line_tag(binding.line_user_id) if binding else web_tag(doc.api_key_id))

        # 記錄起跑時刻；成功完成後才清除這份文件上次留下、本次沒被 upsert 到的舊 wiki pages
        ingest_start = datetime.utcnow()
        await db.commit()

        try:
            suffix = Path(doc.file_path).suffix.lower()
            is_image = suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp")

            # 統一抽全文：圖片用 vision model 做 OCR + 描述，其他用 pypdf / 純文字讀檔。
            # 抽完之後一律走同一條 structured_call pipeline，套用同一份 INGEST_SYSTEM_PROMPT 規則。
            full_text = ""
            if suffix == ".pdf":
                full_text = extract_pdf_text(doc.file_path)
            elif is_image:
                full_text = await vision_extract_text(doc.file_path)
            else:
                full_text = await read_text_file(doc.file_path)

            use_chunked = len(full_text) > LARGE_DOC_CHAR_THRESHOLD

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
                # 統一單次 path：不分圖片/PDF/文字，都已轉成 full_text，走同一條
                existing_result = await db.execute(
                    select(WikiPage).where(WikiPage.api_key_id == doc.api_key_id)
                )
                existing_pages = existing_result.scalars().all()
                existing_context = build_existing_wiki_context(existing_pages)

                prefix = f"{existing_context}\n\n" if existing_context else ""
                source_hint = "（圖片內容已轉文字）" if is_image else ""
                user_content = f"{prefix}文件名稱: {doc.filename}{source_hint}\n\n{full_text}"

                try:
                    single_result: IngestResult = await structured_call(
                        schema=IngestResult,
                        system=INGEST_SYSTEM_PROMPT,
                        user=user_content,
                        max_tokens=32768,
                    )
                    data = single_result.model_dump()
                    if not data.get("pages"):
                        raise ValueError("structured_call returned no pages")
                except Exception as e:
                    # 結構化失敗或回空頁：不要讓 vision 抽出的內容白白丟掉，
                    # 退回保底——把 full_text 整段當一個 stub 頁面存進來，使用者至少看得到內容。
                    import logging
                    logging.getLogger(__name__).warning(
                        "structured_call failed/empty for doc %s; saving stub page: %s",
                        doc.id, e,
                    )
                    stub_title = Path(doc.filename).stem or doc.filename or "未命名"
                    data = {
                        "pages": [{
                            "title": stub_title,
                            "slug": slugify(stub_title),
                            "page_type": "summary",
                            "summary": (full_text or "").strip().replace("\n", " ")[:150]
                                or "（內容尚未結構化）",
                            "content": full_text or "（無內容）",
                            "links_to": [],
                        }],
                        "summary": f"自動 fallback：{stub_title}",
                    }
                doc_summary = data.get("summary", "")
                all_pages_created = await _apply_ingest_result(
                    db, doc.id, doc.api_key_id, data,
                )

            # Code-side 兜底：保證本文件有一個 doc-level 索引頁可供 query 路由。
            # 必須在 stale cleanup 之前跑，靠新的 updated_at 把舊索引保住、避免被刪。
            try:
                doc_index = await synthesize_doc_index_page(
                    db, doc, all_pages_created, doc_summary,
                )
                if doc_index:
                    all_pages_created.append(doc_index)
                    await db.commit()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "synthesize_doc_index_page failed for doc %s: %s", doc.id, e,
                )

            # 成功完成：清理本份文件曾貢獻、但這次沒被 upsert 到的 stale 頁面。
            # 多對多版本：移除這份文件對該頁的 source 連結；若該頁已無任何 source，才刪頁。
            stale_pages_result = await db.execute(
                select(WikiPage)
                .join(WikiPageSource, WikiPage.id == WikiPageSource.wiki_page_id)
                .where(
                    WikiPageSource.document_id == doc.id,
                    WikiPage.updated_at < ingest_start,
                )
            )
            for stale_page in stale_pages_result.scalars().all():
                await db.execute(
                    delete(WikiPageSource).where(
                        WikiPageSource.wiki_page_id == stale_page.id,
                        WikiPageSource.document_id == doc.id,
                    )
                )
                remaining = await db.scalar(
                    select(func.count())
                    .select_from(WikiPageSource)
                    .where(WikiPageSource.wiki_page_id == stale_page.id)
                )
                if remaining == 0:
                    await db.delete(stale_page)
            await db.commit()

            # Active back-linking：掃描既有頁，補上應指向新頁的 cross-reference
            back_link_edits: list[dict] = []
            try:
                new_slugs = {p["slug"] for p in all_pages_created}
                back_link_edits = await back_link_pass(db, doc.api_key_id, new_slugs)
            except Exception as e:
                # back-link 失敗不影響 ingest 主流程，只記 log
                import logging
                logging.getLogger(__name__).warning(
                    "back_link_pass failed for doc %s: %s", doc.id, e
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
                    "back_link_edits": back_link_edits,
                },
            ))
            await db.commit()

        except Exception as e:
            doc.status = "error"
            doc.error_message = str(e)
            await db.commit()
            raise
