import json
import re
import uuid
from datetime import datetime
from typing import AsyncIterator, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.wiki_page import WikiPage
from app.models.wiki_link import WikiLink
from app.models.activity_log import ActivityLog
from app.services.llm import call_llm, stream_llm, structured_call
from app.core.config import get_settings

settings = get_settings()


class PageSelection(BaseModel):
    relevant_slugs: list[str] = Field(
        default_factory=list,
        description="與問題最相關的 wiki 頁面 slug 列表（最多 8 個）",
    )


class SaveJudgment(BaseModel):
    save: bool = Field(description="此問答是否值得存回 wiki")
    reason: str = Field(description="簡短判斷理由")


class PageEdit(BaseModel):
    action: Literal["update", "create"] = Field(
        description="update=改寫既有頁（slug 須存在於既有頁），create=建新 entity/concept 頁"
    )
    slug: str = Field(description="kebab-case slug；update 用既有 slug，create 用新 slug")
    title: str = Field(description="頁面標題")
    page_type: Literal["entity", "concept"] = Field(
        description="entity=人名/組織/產品，concept=概念/技術"
    )
    content: str = Field(description="完整 Markdown 內容；update 時為整合後新版本（保留原精髓+新資訊）")
    reason: str = Field(description="此編輯動機")


class RefinePlan(BaseModel):
    edits: list[PageEdit] = Field(
        default_factory=list,
        description="要套用的編輯列表；若 Q&A 無新資訊可整合則回傳空列表",
    )
    summary: str = Field(description="整體說明此 plan 做什麼（或為何 skip）")


# Phase 1：讓 LLM 從標題列表挑出相關頁面
SELECT_PAGES_PROMPT = """你是一個知識庫搜尋助手。
給定一個問題和 wiki 頁面列表，挑出最相關的頁面（最多 8 個）。
只回傳 slug 列表；若沒有相關頁面，回傳空列表。
"""

# Phase 2：用選出的頁面完整內容來回答
QUERY_SYSTEM_PROMPT = """你是一個個人知識庫助理。
以下是使用者的個人 wiki 頁面內容（以 XML 標籤分隔）。
請根據這些 wiki 內容回答使用者的問題。

規則：
- 優先根據 wiki 內容回答，若 wiki 沒有相關資訊請明確說明
- 回答使用 Markdown 格式
- 引用到的 wiki 頁面請用 [[頁面標題]] 標記
- 回答要清楚、有條理
"""

# Phase 3：判斷是否值得存回 wiki
JUDGE_SAVE_PROMPT = """你是一個知識庫策展人，負責判斷某個問答是否值得加入 wiki。

判斷標準：
- save=true：問答產生了新知識、具通用參考價值、能補充既有 wiki 缺口
- save=false：閒聊、重複既有內容、過於瑣碎、一次性查詢、wiki 已覆蓋
"""

# Phase 4：策展（Curate / Lint）— 決定如何把新知識整合進 wiki
REFINE_SYSTEM_PROMPT = """你是個人 wiki 的策展人（curator）。
你的任務是把一段 Q&A 得到的新知識整合進既有 wiki，讓 wiki 自我精煉。

輸入：
- 使用者問題 + LLM 回答
- 既有相關 wiki 頁面（含完整內容）

請輸出一組 edits（對 wiki 頁面的編輯動作）：
- action=update：改寫某個既有頁（slug 必須是輸入中出現過的既有頁 slug）
  → content: 把新資訊整合進原頁，保留原本精髓，以 Markdown 撰寫，使用 [[頁面標題]] 跨頁連結
- action=create：建立新 entity/concept 頁（僅當新資訊無法併入任一既有頁時）
  → slug: 英文 kebab-case，避免與既有頁衝突
  → page_type: entity（人/組織/產品）或 concept（概念/技術）

規則：
- 優先 update，不要動不動 create
- 若 Q&A 沒新資訊、或資訊已完整存在於既有頁，edits 回傳空列表（即 skip）
- 絕對不要建立 "Q: xxx" 或 "query-xxx" 這類 Q&A 紀錄頁
- 每個 edit 都要給 reason 說明動機
- 一次可以產多個 edit（例如 update 兩頁 + create 一新 concept 頁）
"""


async def refine_wiki_plan(
    question: str,
    answer: str,
    referenced_pages: list[WikiPage],
) -> RefinePlan:
    """用 LLM 產生 refine plan：要 update 哪些既有頁 / create 哪些新頁。"""
    if referenced_pages:
        pages_ctx = "\n\n".join(
            f"<page slug=\"{p.slug}\" type=\"{p.page_type}\" title=\"{p.title}\">\n{p.content}\n</page>"
            for p in referenced_pages
        )
    else:
        pages_ctx = "(無既有相關頁)"

    user_msg = (
        f"問題：{question}\n\n"
        f"回答：{answer}\n\n"
        f"既有相關頁面：\n{pages_ctx}"
    )
    return await structured_call(
        schema=RefinePlan,
        system=REFINE_SYSTEM_PROMPT,
        user=user_msg,
        max_tokens=16384,
    )


async def apply_refine_plan(
    plan: RefinePlan,
    api_key_id: uuid.UUID,
    db: AsyncSession,
) -> list[dict]:
    """套用 refine plan，回傳實際執行的 edits（略過無效 slug）。"""
    applied: list[dict] = []
    for edit in plan.edits:
        if edit.action == "update":
            existing = await db.execute(
                select(WikiPage).where(
                    WikiPage.api_key_id == api_key_id,
                    WikiPage.slug == edit.slug,
                )
            )
            page = existing.scalar_one_or_none()
            if not page:
                continue  # LLM 幻覺了不存在的 slug
            page.content = edit.content
            page.title = edit.title
            page.page_type = edit.page_type
            page.updated_at = datetime.utcnow()
        else:  # create
            check = await db.execute(
                select(WikiPage).where(
                    WikiPage.api_key_id == api_key_id,
                    WikiPage.slug == edit.slug,
                )
            )
            if check.scalar_one_or_none():
                continue  # slug 已存在，避免誤覆蓋
            page = WikiPage(
                api_key_id=api_key_id,
                title=edit.title,
                slug=edit.slug,
                content=edit.content,
                page_type=edit.page_type,
            )
            db.add(page)
        await db.flush()
        applied.append({
            "action": edit.action,
            "slug": edit.slug,
            "title": edit.title,
            "page_type": edit.page_type,
            "reason": edit.reason,
        })
    return applied


async def judge_save_decision(
    question: str,
    answer: str,
    referenced_pages: list[dict],
) -> tuple[bool, str]:
    """請 LLM 判斷此問答是否值得存入 wiki。回傳 (save, reason)。"""
    refs = "\n".join(f"- {p['title']} ({p['slug']})" for p in referenced_pages) or "(無)"
    user_msg = (
        f"問題：{question}\n\n"
        f"回答：{answer[:1500]}\n\n"
        f"參考頁面：\n{refs}"
    )
    try:
        result = await structured_call(
            schema=SaveJudgment,
            system=JUDGE_SAVE_PROMPT,
            user=user_msg,
            max_tokens=512,
        )
        return result.save, result.reason
    except Exception as e:
        return False, f"判斷失敗：{e}"


def _keyword_match(question: str, pages: list[WikiPage], limit: int) -> list[WikiPage]:
    """以問題詞彙粗略比對 title+content，分數高者優先。"""
    tokens = [t for t in re.split(r"[\s,.，。、?？!！:：;；]+", question) if len(t) >= 2]
    if not tokens:
        return []
    scored: list[tuple[int, WikiPage]] = []
    for p in pages:
        hay = f"{p.title}\n{p.content or ''}".lower()
        score = sum(hay.count(t.lower()) for t in tokens)
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:limit]]


async def select_relevant_pages(
    question: str,
    all_pages: list[WikiPage],
    max_pages: int | None = None,
) -> list[WikiPage]:
    """
    Phase 1：小型 wiki（≤max_pages）全量帶入；否則用 LLM 挑選。
    index 帶 content 摘要讓 LLM 有線索，並以關鍵字比對作 fallback。
    """
    if not all_pages:
        return []

    if max_pages is None:
        max_pages = settings.MAX_WIKI_PAGES

    # 上傳時已限制 wiki 頁數 ≤ MAX_WIKI_PAGES，理論上全量帶入
    if len(all_pages) <= max_pages:
        return list(all_pages)

    # 送 title + slug + content 摘要（前 300 字）給 LLM
    def _summary(p: WikiPage) -> str:
        return (p.content or "").strip().replace("\n", " ")[:300]

    index = "\n".join(
        f"- slug: \"{p.slug}\" | title: \"{p.title}\" | type: {p.page_type} | summary: {_summary(p)}"
        for p in all_pages
    )

    try:
        result = await structured_call(
            schema=PageSelection,
            system=SELECT_PAGES_PROMPT,
            user=f"問題：{question}\n\nWiki 頁面列表：\n{index}",
            max_tokens=1024,
        )
        relevant_slugs = set(result.relevant_slugs)
    except Exception:
        relevant_slugs = set()

    selected = [p for p in all_pages if p.slug in relevant_slugs]

    # Fallback 1: LLM 沒選任何頁面 → 用關鍵字比對
    if not selected:
        selected = _keyword_match(question, all_pages, max_pages)

    # Fallback 2: 還是沒選到 → 最新 max_pages 頁
    if not selected:
        selected = sorted(all_pages, key=lambda p: p.updated_at, reverse=True)[:max_pages]

    return selected[:max_pages]


async def run_query(
    question: str,
    api_key_id: uuid.UUID,
    db: AsyncSession,
    save_to_wiki: bool = False,
) -> dict:
    """執行查詢流程"""
    # 取所有頁面標題，讓 LLM 決定哪些相關
    all_result = await db.execute(
        select(WikiPage).where(WikiPage.api_key_id == api_key_id)
    )
    all_pages = all_result.scalars().all()
    pages = await select_relevant_pages(question, all_pages)

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


async def run_query_stream(
    question: str,
    api_key_id: uuid.UUID,
    db: AsyncSession,
) -> AsyncIterator[str]:
    """串流版 query：以 NDJSON 格式 yield 事件。
    流程：Phase 1 選頁 → Phase 2 串流回答 → Phase 3 判斷存 → Phase 4 策展整合
    事件類型：
      - {"type":"pages","pages":[...]}                        phase 1 結果
      - {"type":"chunk","content":"..."}                      LLM 輸出的 delta（含 <think>）
      - {"type":"judge","save":bool,"reason":"..."}           Phase 3 判斷結果
      - {"type":"refine","edits":[...],"summary":"..."}       Phase 4 策展結果
      - {"type":"done"}                                        完成
      - {"type":"error","message":"..."}                      錯誤
    """
    try:
        all_result = await db.execute(
            select(WikiPage).where(WikiPage.api_key_id == api_key_id)
        )
        all_pages = all_result.scalars().all()
        pages = await select_relevant_pages(question, all_pages)

        referenced_pages = [{"id": str(p.id), "title": p.title, "slug": p.slug} for p in pages]
        yield json.dumps({"type": "pages", "pages": referenced_pages}) + "\n"

        wiki_context = "\n\n".join(
            f"<wiki_page title='{p.title}' slug='{p.slug}'>\n{p.content}\n</wiki_page>"
            for p in pages
        )
        system = f"{QUERY_SYSTEM_PROMPT}\n\n<wiki>\n{wiki_context}\n</wiki>"

        full_answer_parts: list[str] = []
        async for delta in stream_llm(
            system=system,
            messages=[{"role": "user", "content": question}],
            max_tokens=4096,
        ):
            full_answer_parts.append(delta)
            yield json.dumps({"type": "chunk", "content": delta}) + "\n"

        answer = "".join(full_answer_parts)
        # 剝除 <think>...</think> 區塊以免存回 wiki
        answer_clean = re.sub(r"<think>[\s\S]*?</think>", "", answer).strip()

        # Phase 3：判斷是否值得存回 wiki
        save_decision, save_reason = await judge_save_decision(question, answer_clean, referenced_pages)
        yield json.dumps({"type": "judge", "save": save_decision, "reason": save_reason}) + "\n"

        applied_edits: list[dict] = []
        refine_summary = ""
        if save_decision:
            try:
                plan = await refine_wiki_plan(question, answer_clean, pages)
                refine_summary = plan.summary
                applied_edits = await apply_refine_plan(plan, api_key_id, db)
            except Exception as e:
                refine_summary = f"refine 失敗：{e}"
            yield json.dumps({
                "type": "refine",
                "edits": applied_edits,
                "summary": refine_summary,
            }) + "\n"

        db.add(ActivityLog(
            api_key_id=api_key_id,
            action="query",
            details={
                "question": question,
                "pages_referenced": len(pages),
                "save_decision": save_decision,
                "save_reason": save_reason,
                "refine_summary": refine_summary,
                "edits_applied": applied_edits,
            },
        ))
        await db.commit()

        yield json.dumps({"type": "done"}) + "\n"

    except Exception as e:
        yield json.dumps({"type": "error", "message": str(e)}) + "\n"
