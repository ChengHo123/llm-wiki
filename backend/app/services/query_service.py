import json
import re
import uuid
from datetime import datetime
from typing import AsyncIterator, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.wiki_page import WikiPage
from app.models.activity_log import ActivityLog
from app.services.llm import call_llm, stream_llm, structured_call
from app.core.config import get_settings

settings = get_settings()


class RouteDecision(BaseModel):
    need_wiki: bool = Field(description="是否需要查 wiki 才能回答")
    reason: str = Field(description="簡短判斷理由")


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


# Phase 0：路由 — 判斷此訊息是否需要查 wiki
ROUTER_SYSTEM_PROMPT = """你是對話路由助手。判斷使用者訊息是否需要查 wiki 知識庫才能好好回答。

判斷標準：
- need_wiki=true：訊息問及 wiki 內可能存在的事實、人物、組織、概念、技術、專有名詞、定義、流程，或語意上明顯指涉某個列表中的條目
- need_wiki=false：純閒聊、問候、感受、自我介紹、不相關的常識問題、玩笑、撒嬌

如果使用者明確提到 wiki 標題列表中出現的任何詞、或語意相近的詞 → need_wiki=true
如果列表為空，或訊息完全與列表內容無關 → need_wiki=false
寧可在邊界情況下選 true（多查不傷，沒查到會誤事）。
"""


CHAT_ONLY_SYSTEM_PROMPT = """你正在跟使用者自然聊天，不需要查任何資料庫。
不要假裝自己有 wiki，也不要在回答中提到任何頁面或連結。
回答簡短、自然、貼近對話脈絡。
"""


# Phase 1：讓 LLM 從標題列表挑出相關頁面
SELECT_PAGES_PROMPT = """你是一個知識庫搜尋助手。
給定一個問題和 wiki 頁面列表，挑出**所有**與問題相關的頁面，**數量不限**。
寧可多選不要漏；單一文件（例如一本小說）的多個相關頁面要全部選出，避免回答時資訊不完整。
只回傳 slug 列表；若真的完全無相關，回傳空列表。
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

# Wiki 靈魂：所有策展類 LLM 都要先讀這段，避免方向走偏
WIKI_SOUL = """## 這個 wiki 的靈魂

- wiki 是「蒸餾後的結構化知識」，不是對話存檔，也不是搜尋紀錄
- 每頁有明確 entity/concept，互相交叉連結，追求知識複利累積
- 知識主要來源是使用者主動餵入的文件；聊天只能「補充」既有方向，不能「主導」wiki 走向
- 你的職責是守門人，不是照單全收。寧可拒絕，也不要讓低品質內容稀釋 wiki 價值
"""


# Phase 3：判斷是否值得存回 wiki
JUDGE_SAVE_PROMPT = WIKI_SOUL + """
## 你的任務
判斷一段聊天問答是否值得整合進 wiki。

## save=true 的情況（任一滿足）
1. 答案揭露既有頁面之間的**新連結**（A 和 B 原本沒交叉引用，但答案說明了關係）
2. 答案對某個既有頁面有**實質補充**（新 context / 細節 / 修正，且與原頁不重複）
3. 答案合成出一個**還沒有獨立頁面的新概念**，且概念足夠獨立、普適

## save=false 的硬性拒絕條件（任一命中即拒）
- 答案只是重述現有 wiki 頁面內容，無增量價值
- 低信心 / 模糊推測（出現「可能」「不太確定」「也許」「我猜」）
- 問題屬個人事務 / 閒聊 / 不屬知識庫主題
- 答案與 wiki 既有知識方向相悖，可能是錯誤資訊
- 問題本身已有完整 wiki 頁面可以直接回答

寧可錯殺，不可讓垃圾汙染 wiki。
"""


# Phase 4：策展（Curate）— 決定如何把新知識整合進 wiki
REFINE_SYSTEM_PROMPT = WIKI_SOUL + """
## 你的任務
把一段 Q&A 的新知識，**忠於 wiki 靈魂地**整合進既有 wiki。

## 輸入
- 使用者問題 + LLM 回答
- 既有相關 wiki 頁面（含完整內容）

## 三種正確做法（對應到 action）
1. **update（補充既有頁）**：答案對某既有頁有新 context / 細節 / 連結 → 改寫該頁，**保留原精髓**，自然嵌入新資訊與 [[跨頁連結]]
2. **create（新建頁面）**：答案合成出一個獨立、普適的新 entity/concept，且**真的找不到既有頁可併入**
3. **skip（不存）**：edits 回傳空列表

## 嚴格規則
- 優先 update，慎用 create（每多一個低價值頁面都在汙染 wiki）
- update 的 slug 必須是輸入中既有頁的 slug，不准創新
- create 的 slug 要英文 kebab-case，且避免和既有頁衝突
- page_type 只能是 entity（人/組織/產品）或 concept（概念/技術）
- **絕對禁止**建立 "Q: xxx"、"query-xxx"、"chat-xxx"、"如何xxx" 這類 Q&A 風格頁面
- update 時 content 要是**整合後完整新版本**，不是 diff、不是片段
- 每個 edit 都要給 reason 說明動機，方便事後追蹤
- 不確定要不要存 → skip。寧可漏存，不可亂建頁
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


def _trim_history(
    history: list[dict] | None,
    max_turns: int = 20,
    max_chars: int = 800,
) -> list[dict]:
    """限制 history 規模避免 prompt 爆炸。預設保留最近 20 則 (10 turns)，每則 800 字。"""
    if not history:
        return []
    out = []
    for m in history[-max_turns:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = (m.get("content") or "")[:max_chars]
        if not content.strip():
            continue
        out.append({"role": role, "content": content})
    return out


async def route_query(
    question: str,
    all_pages: list[WikiPage],
    history: list[dict] | None = None,
) -> RouteDecision:
    """Phase 0：判斷是否需要查 wiki。空 wiki / 路由失敗都直接走 chat-only。"""
    if not all_pages:
        return RouteDecision(need_wiki=False, reason="wiki 為空")
    summary = "\n".join(
        f"- {p.title} ({p.page_type})" for p in all_pages[:100]
    )
    history_text = ""
    recent = (history or [])[-4:]  # 給 router 最近 2 turns 判斷代名詞 / 追問
    if recent:
        lines = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in recent)
        history_text = f"\n\n最近對話（脈絡用，幫忙判斷代名詞 / 追問是否承接 wiki 主題）：\n{lines}"
    user_msg = f"使用者最新訊息：{question}{history_text}\n\nWiki 頁面列表：\n{summary}"
    try:
        return await structured_call(
            schema=RouteDecision,
            system=ROUTER_SYSTEM_PROMPT,
            user=user_msg,
            max_tokens=512,
        )
    except Exception as e:
        # 路由失敗保守一點走 wiki path
        return RouteDecision(need_wiki=True, reason=f"路由失敗：{e}")


async def chat_only_reply(
    question: str,
    persona: str = "",
    history: list[dict] | None = None,
) -> str:
    system = CHAT_ONLY_SYSTEM_PROMPT
    if persona:
        system = f"{system}\n\n<persona>\n{persona}\n</persona>"
    return await call_llm(
        system=system,
        messages=[*(history or []), {"role": "user", "content": question}],
        max_tokens=1024,
    )


def _keyword_match(
    question: str, pages: list[WikiPage], limit: int | None = None
) -> list[WikiPage]:
    """以問題詞彙粗略比對 title+content，分數高者優先。limit=None 表示不限制（回傳所有 score>0）。"""
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
    matched = [p for _, p in scored]
    return matched[:limit] if limit is not None else matched


async def select_relevant_pages(
    question: str,
    all_pages: list[WikiPage],
    max_pages: int | None = None,
) -> list[WikiPage]:
    """
    Phase 1：小型 wiki（≤max_pages）全量帶入；否則用 LLM 挑選**所有相關頁面**（不再硬截斷）。
    index 帶 content 摘要讓 LLM 有線索，並以關鍵字比對作 fallback。
    """
    if not all_pages:
        return []

    if max_pages is None:
        max_pages = settings.MAX_WIKI_PAGES

    # 小型 wiki 直接全量帶入；但仍受 MAX_QUERY_CONTEXT_PAGES 上限保護
    if len(all_pages) <= max_pages:
        context_cap = settings.MAX_QUERY_CONTEXT_PAGES
        if len(all_pages) <= context_cap:
            return list(all_pages)
        # 全量超過 context cap → 退回 LLM 選擇路徑做篩選

    # 送 title + slug + 預先計算的 summary 給 LLM；若 summary 空（舊資料未補）退回用 content[:300]
    def _summary(p: WikiPage) -> str:
        if p.summary and p.summary.strip():
            return p.summary.strip().replace("\n", " ")[:300]
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
        selected = _keyword_match(question, all_pages)

    # Fallback 2: 真的零匹配 → 最新 max_pages 頁（沒任何線索時的最後一招）
    if not selected:
        selected = sorted(all_pages, key=lambda p: p.updated_at, reverse=True)[:max_pages]

    # 硬上限：超過 MAX_QUERY_CONTEXT_PAGES 就截斷，避免 prompt 太大爆 context window
    context_cap = settings.MAX_QUERY_CONTEXT_PAGES
    if len(selected) > context_cap:
        # 優先保留 LLM 選中的（順序視同重要性），不夠才用其他 fallback；都不夠就截斷
        selected = selected[:context_cap]

    return selected


async def run_query(
    question: str,
    api_key_id: uuid.UUID,
    db: AsyncSession,
    save_to_wiki: bool = False,
    persona: str = "",
    history: list[dict] | None = None,
) -> dict:
    """執行查詢流程。persona 會附加在 system prompt 之後，可用來指定角色口吻。
    history: 最近的對話訊息 [{role, content}]，會傳給 LLM 提供脈絡。"""
    history = _trim_history(history)

    # 取所有頁面標題，讓 LLM 決定哪些相關
    all_result = await db.execute(
        select(WikiPage).where(WikiPage.api_key_id == api_key_id)
    )
    all_pages = all_result.scalars().all()

    # Phase 0：路由 — 不需查 wiki 直接走 chat-only
    decision = await route_query(question, all_pages, history)
    if not decision.need_wiki:
        answer = await chat_only_reply(question, persona, history)
        # 不存 question / route_reason 等 LLM 自由文字，避免洩漏使用者隱私
        db.add(ActivityLog(
            api_key_id=api_key_id,
            action="chat",
            details={},
        ))
        await db.commit()
        return {
            "answer": answer,
            "referenced_pages": [],
            "saved_page": None,
            "wiki_save": None,
            "route": {"need_wiki": False, "reason": decision.reason},
        }

    pages = await select_relevant_pages(question, all_pages)

    # 建立 wiki context — 動態減枝：先試 content，塞不下退用 summary，再不行就截斷。
    # 字元上限粗估 token budget；模型多為 32K context，留約 8K 給 system+question+answer。
    MAX_CONTEXT_CHARS = 60000
    context_parts: list[str] = []
    remaining = MAX_CONTEXT_CHARS
    degraded_count = 0
    for p in pages:
        full = p.content or ""
        summary = (p.summary or "").strip()
        if len(full) <= remaining and full:
            body = full
            remaining -= len(full)
        elif summary and len(summary) <= remaining:
            body = summary
            remaining -= len(summary)
            degraded_count += 1
        elif remaining > 0:
            body = full[:remaining] if full else summary[:remaining]
            remaining = 0
            degraded_count += 1
        else:
            break
        context_parts.append(
            f"<wiki_page title='{p.title}' slug='{p.slug}'>\n{body}\n</wiki_page>"
        )
    wiki_context = "\n\n".join(context_parts)
    if degraded_count:
        import logging
        logging.getLogger(__name__).info(
            "wiki_context degraded: %d/%d pages used summary/truncated",
            degraded_count, len(pages),
        )

    system = f"{QUERY_SYSTEM_PROMPT}\n\n<wiki>\n{wiki_context}\n</wiki>"
    if persona:
        system = f"{system}\n\n<persona>\n{persona}\n</persona>"

    answer = await call_llm(
        system=system,
        messages=[*history, {"role": "user", "content": question}],
        max_tokens=2048,
    )

    referenced_pages = [{"id": str(p.id), "title": p.title, "slug": p.slug} for p in pages]

    # 選擇性存回 wiki — 走 wiki manager 流程：judge → refine → apply
    wiki_save = None
    if save_to_wiki:
        save_decision, save_reason = await judge_save_decision(question, answer, referenced_pages)
        applied_edits: list[dict] = []
        refine_summary = ""
        if save_decision:
            try:
                plan = await refine_wiki_plan(question, answer, pages)
                refine_summary = plan.summary
                applied_edits = await apply_refine_plan(plan, api_key_id, db)
            except Exception as e:
                refine_summary = f"refine 失敗：{e}"
        wiki_save = {
            "save_decision": save_decision,
            "judge_reason": save_reason,
            "applied_edits": applied_edits,
            "refine_summary": refine_summary,
        }

    db.add(ActivityLog(
        api_key_id=api_key_id,
        action="query",
        details={
            "pages_referenced": len(pages),
            "save_decision": (wiki_save or {}).get("save_decision"),
            "edits_applied": (wiki_save or {}).get("applied_edits", []),
        },
    ))
    await db.commit()

    return {
        "answer": answer,
        "referenced_pages": referenced_pages,
        "saved_page": None,  # 多頁編輯不再單一返回，保留欄位給 schema 相容
        "wiki_save": wiki_save,
    }


async def run_query_stream(
    question: str,
    api_key_id: uuid.UUID,
    db: AsyncSession,
    history: list[dict] | None = None,
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
        history_clean = _trim_history(history)

        all_result = await db.execute(
            select(WikiPage).where(WikiPage.api_key_id == api_key_id)
        )
        all_pages = all_result.scalars().all()

        # Phase 0：路由
        decision = await route_query(question, all_pages, history_clean)
        yield json.dumps({
            "type": "route",
            "need_wiki": decision.need_wiki,
            "reason": decision.reason,
        }) + "\n"

        if not decision.need_wiki:
            yield json.dumps({"type": "pages", "pages": []}) + "\n"
            async for delta in stream_llm(
                system=CHAT_ONLY_SYSTEM_PROMPT,
                messages=[*history_clean, {"role": "user", "content": question}],
                max_tokens=1024,
            ):
                yield json.dumps({"type": "chunk", "content": delta}) + "\n"
            db.add(ActivityLog(
                api_key_id=api_key_id,
                action="chat",
                details={},
            ))
            await db.commit()
            yield json.dumps({"type": "done"}) + "\n"
            return

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
            messages=[*history_clean, {"role": "user", "content": question}],
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
                "pages_referenced": len(pages),
                "save_decision": save_decision,
                "edits_applied": applied_edits,  # 只有 wiki 頁的 slug/title，已在 wiki_pages 表中
            },
        ))
        await db.commit()

        yield json.dumps({"type": "done"}) + "\n"

    except Exception as e:
        yield json.dumps({"type": "error", "message": str(e)}) + "\n"
