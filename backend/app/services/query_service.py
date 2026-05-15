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
from app.services.wiki_links import reconcile_wiki_links_from_content
from app.core.config import get_settings

settings = get_settings()


class RouteDecision(BaseModel):
    need_wiki: bool = Field(description="是否需要查 wiki 才能回答")
    reason: str = Field(description="簡短判斷理由")


class PageSelection(BaseModel):
    relevant_slugs: list[str] = Field(
        default_factory=list,
        description="作為查詢進入點的 anchor 頁面 slug 列表，挑 1~5 個就好；系統會自動展開鄰居",
    )


class SaveJudgment(BaseModel):
    save: bool = Field(description="此問答是否值得存回 wiki")
    reason: str = Field(description="簡短判斷理由")


class PageEdit(BaseModel):
    action: Literal["update", "create"] = Field(
        description="update=改寫既有頁（slug 須存在於既有頁），create=建新頁"
    )
    slug: str = Field(description="kebab-case slug；update 用既有 slug，create 用新 slug")
    title: str = Field(description="頁面標題")
    page_type: Literal["entity", "concept", "index"] = Field(
        description="entity=人名/組織/產品，concept=概念/技術，index=主題索引頁（條列 [[頁面]] 入口）"
    )
    summary: str = Field(
        default="",
        description="1-2 句、最多 150 字的本頁主題濃縮，用於 wiki 索引；update 時也應更新",
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


# Phase 1A：純 index 路由（首選）。只把 page_type='index' 的頁面送給 LLM，挑進入點，
# 系統自動沿 wiki_links 1-hop 擴展，把 index 指向的 entity/concept 帶進 context。
SELECT_INDEX_PROMPT = """你是一個 LLM Wiki 的查詢路由。
給定一個問題和 wiki 的**索引頁列表**（每個索引頁是一群主題頁的入口），挑出 1~3 個**最直接命中問題核心**的索引頁。

設計原則：
- 系統會自動沿 [[wikilink]] 1-hop，把你選的索引頁所指向的主題頁全部帶進回答 context
- 索引頁的價值就是它**指向一整批相關主題**；選對索引頁等於選對整個主題網絡
- 不需要挑全部相關索引，挑最核心的就好

判斷標準：
- 問題明確指涉某個索引主題 → 該索引就是 anchor
- 問題綜合性 → 挑 2 個各代表一個面向
- 問題曖昧 → 寧可挑 2 個，不要全選

只回傳 slug 列表；若沒任何索引頁適合，回傳空列表（系統會 fallback）。
"""

# Phase 1B：fallback。當 wiki 還沒有足夠 index 頁時，退回看全頁 summary 挑 anchor。
SELECT_PAGES_PROMPT = """你是一個 LLM Wiki 的查詢路由。
給定一個問題和 wiki 頁面的 summary 索引，挑出 1~5 個**最直接命中問題核心**的 anchor 頁面。

設計原則：
- 系統會自動把 anchor 的鄰居（透過 [[wikilink]] 連入/連出 1 hop）一起帶進回答 context
- 所以你**不需要挑全部相關頁**，挑最核心的入口就好
- 像查百科一樣：找最相關的條目當入口，剩下靠交叉引用展開

判斷標準：
- 問題明確指涉的實體 / 概念 → 該頁就是 anchor
- 問題是綜合性主題 → 挑代表性的 index 或 summary 頁
- 問題曖昧 → 寧可挑 2-3 個 anchor 各代表一個解讀，不要選 10 幾個

只回傳 slug 列表；若 wiki 完全沒任何相關頁，回傳空列表。
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
- 既有相關 wiki 頁面（含完整內容），含可能的 index 頁

## 編輯類型（對應到 action + page_type）
1. **update entity/concept**：答案對某既有頁有新 context / 細節 / 連結 → 改寫該頁，保留原精髓，自然嵌入新資訊與 [[跨頁連結]]
2. **create entity/concept**：答案合成出一個獨立、普適的新概念，且**真的找不到既有頁可併入**
3. **update index**：新建了 entity/concept 頁，且輸入中有合適的既有 index 頁能納入這個新主題 → update 該 index，content 補上 `- [[新頁標題]]` 條目
4. **create index**：≥3 個相關既有頁（或本次新建頁）沒有 index 串連 → 建一個主題式 index 頁，content 條列 `[[頁面]]` 入口
5. **skip**：edits 回傳空列表

## 嚴格規則
- 優先 update，慎用 create（每多一個低價值頁面都在汙染 wiki）
- update 的 slug 必須是輸入中既有頁的 slug，不准創新
- create 的 slug 要英文 kebab-case，且避免和既有頁衝突
- page_type 只能是 entity / concept / index
- index 頁的 content 必須有 ≥3 個 [[頁面]] 條目；不要建只有 1-2 個連結的 index
- **絕對禁止**建立 "Q: xxx"、"query-xxx"、"chat-xxx"、"如何xxx" 這類 Q&A 風格頁面
- **絕對禁止**自創沒在既有頁列表中的 slug 當作 [[連結]] 目標——只能連既有頁或本批次同時 create 的頁
- update 時 content 要是**整合後完整新版本**，不是 diff、不是片段
- 每個 edit 都要產生 1-2 句、最多 150 字的 `summary`，濃縮本頁重點，供 wiki 索引/路由使用
- 每個 edit 都要給 reason 說明動機，方便事後追蹤
- 若要 create entity/concept 又 update index 把它納入，請把 create 排在 update 之前（系統依序套用）
- 不確定要不要存 → skip。寧可漏存，不可亂建頁
"""


async def refine_wiki_plan(
    question: str,
    answer: str,
    referenced_pages: list[WikiPage],
    all_index_pages: list[WikiPage] | None = None,
) -> RefinePlan:
    """用 LLM 產生 refine plan：要 update 哪些既有頁 / create 哪些新頁。

    referenced_pages 是這輪 query 路由帶進的鄰居頁（含 content）。
    all_index_pages 是 wiki 內所有 page_type='index' 頁（輕量：只 slug+title+summary，不含 content），
    讓 LLM 在決定 update/create index 時看得到全 index 表，不會因為某 index 沒被本輪路由命中就盲建新的。
    """
    if referenced_pages:
        bodies = degrade_page_bodies(referenced_pages, max_chars=40000)
        pages_ctx = "\n\n".join(
            f"<page slug=\"{p.slug}\" type=\"{p.page_type}\" title=\"{p.title}\">\n{body}\n</page>"
            for p, body in zip(referenced_pages, bodies)
        )
    else:
        pages_ctx = "(無既有相關頁)"

    index_ctx = ""
    if all_index_pages:
        referenced_slugs = {p.slug for p in referenced_pages}
        extra_indexes = [p for p in all_index_pages if p.slug not in referenced_slugs]
        if extra_indexes:
            lines = ["<all_index_pages>"]
            for p in extra_indexes:
                s = (p.summary or "").strip().replace("\n", " ")[:200] or p.title
                lines.append(f"- slug: {p.slug} | title: {p.title} | summary: {s}")
            lines.append("</all_index_pages>")
            index_ctx = "\n".join(lines) + "\n\n"

    user_msg = (
        f"問題：{question}\n\n"
        f"回答：{answer}\n\n"
        f"{index_ctx}"
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
    """套用 refine plan，回傳實際執行的 edits（略過無效 slug）。

    每筆 edit 完成後重建該頁的 outgoing wiki_links，確保 [[wikilink]] → DB 邊一致。
    這對 index 頁尤其關鍵：query 路由依賴 wiki_links 從 index 1-hop 展開到主題頁。
    """
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
            if edit.summary:
                page.summary = edit.summary
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
                summary=edit.summary or "",
            )
            db.add(page)
        await db.flush()
        link_count = await reconcile_wiki_links_from_content(db, page, api_key_id)
        applied.append({
            "action": edit.action,
            "slug": edit.slug,
            "title": edit.title,
            "page_type": edit.page_type,
            "reason": edit.reason,
            "links_rebuilt": link_count,
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


def degrade_page_bodies(pages: list[WikiPage], max_chars: int = 60000) -> list[str]:
    """Two-phase context budget：每頁先配 summary（沒 summary 退 content[:300]），
    再用剩餘 budget 把能升級的頁面替換成完整 content。
    回傳和 pages 同長度的 body 字串列表；上層自行包 XML/Markdown。"""
    bodies: list[str] = []
    for p in pages:
        summary = (p.summary or "").strip()
        if summary:
            bodies.append(summary)
        else:
            bodies.append((p.content or "")[:300])
    used = sum(len(b) for b in bodies)
    remaining = max_chars - used
    for i, p in enumerate(pages):
        full = p.content or ""
        if not full or full == bodies[i]:
            continue
        delta = len(full) - len(bodies[i])
        if delta <= remaining:
            bodies[i] = full
            remaining -= delta
    return bodies


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
        # 路由失敗：短訊息（< 20 字）大概率是閒聊，避免硬走 wiki 把 prompt 灌大；
        # 長訊息保守走 wiki，符合原本「多查不傷」原則。
        short = len(question.strip()) < 20
        return RouteDecision(
            need_wiki=not short,
            reason=f"路由失敗（{'短訊息→chat-only' if short else '長訊息→保守走 wiki'}）：{e}",
        )


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


async def maybe_save_chat_to_wiki(
    question: str,
    answer: str,
    api_key_id: uuid.UUID,
    db: AsyncSession,
) -> dict:
    """Chat-only 路徑也跑 judge + refine，讓對話蒸餾出的新概念能複利進 wiki。

    Karpathy 模式核心：「有價值的分析結果存回 wiki，不因對話結束而消失」。
    即使路由判斷不需查 wiki，答案仍可能揭露新概念或既有頁面間關係。judge 嚴格把關
    （JUDGE_SAVE_PROMPT 的硬性拒絕條件能擋掉純閒聊），通過後用 keyword 比對挑出
    可能受影響的既有頁面當 refine context，避免 LLM 在沒看到任何既有結構時盲建新頁。
    """
    save_decision, save_reason = await judge_save_decision(question, answer, [])
    wiki_save: dict = {
        "save_decision": save_decision,
        "judge_reason": save_reason,
        "applied_edits": [],
        "refine_summary": "",
    }
    if not save_decision:
        return wiki_save

    all_result = await db.execute(
        select(WikiPage).where(WikiPage.api_key_id == api_key_id)
    )
    all_pages = all_result.scalars().all()

    # 用答案內容做關鍵字比對挑可能相關的既有頁，當 refine 編輯目標；
    # 同時帶入全部 index 頁，讓 LLM 能 update 對應 index 把新主題納入結構。
    refs = _keyword_match(answer, all_pages, limit=8)
    index_pages = [p for p in all_pages if p.page_type == "index"]

    try:
        plan = await refine_wiki_plan(
            question, answer,
            referenced_pages=refs,
            all_index_pages=index_pages,
        )
        wiki_save["refine_summary"] = plan.summary
        wiki_save["applied_edits"] = await apply_refine_plan(plan, api_key_id, db)
    except Exception as e:
        wiki_save["refine_summary"] = f"refine 失敗：{e}"
    return wiki_save


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


async def _expand_via_wiki_links(
    db: AsyncSession,
    anchor_ids: set,
    all_pages: list[WikiPage],
    hops: int = 1,
) -> list[WikiPage]:
    """從 anchor 沿 wiki_links 走 N hop，回傳 anchor + 鄰居頁面（去重，保留 anchor 順序在前）。"""
    page_by_id = {p.id: p for p in all_pages}
    visited = set(anchor_ids)
    frontier = set(anchor_ids)
    for _ in range(max(0, hops)):
        if not frontier:
            break
        links_result = await db.execute(
            select(WikiLink).where(
                (WikiLink.source_page_id.in_(frontier))
                | (WikiLink.target_page_id.in_(frontier))
            )
        )
        next_frontier = set()
        for lnk in links_result.scalars():
            for nid in (lnk.source_page_id, lnk.target_page_id):
                if nid not in visited and nid in page_by_id:
                    next_frontier.add(nid)
                    visited.add(nid)
        frontier = next_frontier
    # anchor 先排前面，鄰居依 updated_at 後排
    anchors = [page_by_id[i] for i in anchor_ids if i in page_by_id]
    neighbors = [
        p for p in sorted(
            (page_by_id[i] for i in visited if i not in anchor_ids and i in page_by_id),
            key=lambda p: p.updated_at,
            reverse=True,
        )
    ]
    return anchors + neighbors


MIN_INDEX_PAGES_FOR_ROUTING = 2


def _page_summary_blurb(p: WikiPage, max_chars: int = 300) -> str:
    if p.summary and p.summary.strip():
        return p.summary.strip().replace("\n", " ")[:max_chars]
    return (p.content or "").strip().replace("\n", " ")[:max_chars]


async def _route_via_index(
    question: str,
    index_pages: list[WikiPage],
    all_pages: list[WikiPage],
    db: AsyncSession,
    context_cap: int,
) -> list[WikiPage]:
    """A 方案核心：LLM 只看 index 頁挑 anchor，沿 wiki_links 1-hop 展開。"""
    listing = "\n".join(
        f"- slug: \"{p.slug}\" | title: \"{p.title}\" | summary: {_page_summary_blurb(p)}"
        for p in index_pages
    )
    try:
        result = await structured_call(
            schema=PageSelection,
            system=SELECT_INDEX_PROMPT,
            user=f"問題：{question}\n\n索引頁列表：\n{listing}",
            max_tokens=1024,
        )
        anchor_slugs = set(result.relevant_slugs)
    except Exception:
        anchor_slugs = set()

    anchor_pages = [p for p in index_pages if p.slug in anchor_slugs]

    # Fallback 1：LLM 沒選 → index 頁內關鍵字比對
    if not anchor_pages:
        anchor_pages = _keyword_match(question, index_pages, limit=3)

    # Fallback 2：仍零中 → 最新的一個 index 頁
    if not anchor_pages:
        anchor_pages = sorted(index_pages, key=lambda p: p.updated_at, reverse=True)[:1]

    anchor_ids = {p.id for p in anchor_pages}
    selected = await _expand_via_wiki_links(db, anchor_ids, all_pages, hops=1)
    if len(selected) > context_cap:
        selected = selected[:context_cap]
    return selected


async def _route_via_summary(
    question: str,
    all_pages: list[WikiPage],
    db: AsyncSession | None,
    context_cap: int,
) -> list[WikiPage]:
    """Fallback 路由：wiki 還沒累積足夠 index 頁時用。看全頁 summary 挑 anchor，沿 links 擴展。"""
    index = "\n".join(
        f"- slug: \"{p.slug}\" | title: \"{p.title}\" | type: {p.page_type} | summary: {_page_summary_blurb(p)}"
        for p in all_pages
    )
    try:
        result = await structured_call(
            schema=PageSelection,
            system=SELECT_PAGES_PROMPT,
            user=f"問題：{question}\n\nWiki 頁面 summary 索引：\n{index}",
            max_tokens=1024,
        )
        anchor_slugs = set(result.relevant_slugs)
    except Exception:
        anchor_slugs = set()

    anchor_pages = [p for p in all_pages if p.slug in anchor_slugs]
    if not anchor_pages:
        anchor_pages = _keyword_match(question, all_pages, limit=5)
    if not anchor_pages:
        anchor_pages = sorted(all_pages, key=lambda p: p.updated_at, reverse=True)[:5]

    anchor_ids = {p.id for p in anchor_pages}
    if db is None:
        selected = anchor_pages
    else:
        selected = await _expand_via_wiki_links(db, anchor_ids, all_pages, hops=1)
    if len(selected) > context_cap:
        selected = selected[:context_cap]
    return selected


async def select_relevant_pages(
    question: str,
    all_pages: list[WikiPage],
    db: AsyncSession | None = None,
    max_pages: int | None = None,
) -> list[WikiPage]:
    """
    LLM Wiki 風格 query 路由：
    - 首選：只把 page_type='index' 頁送 LLM 挑 anchor，沿 wiki_links 1-hop 展開
    - Fallback：wiki 還沒累積 >=2 個 index 頁時，退回全頁 summary 模式（舊行為）
    - 小型 wiki（≤ context_cap）直接全量帶入，不路由
    """
    if not all_pages:
        return []

    if max_pages is None:
        max_pages = settings.MAX_WIKI_PAGES
    context_cap = settings.MAX_QUERY_CONTEXT_PAGES

    if len(all_pages) <= context_cap:
        return list(all_pages)

    index_pages = [p for p in all_pages if p.page_type == "index"]

    if db is not None and len(index_pages) >= MIN_INDEX_PAGES_FOR_ROUTING:
        return await _route_via_index(question, index_pages, all_pages, db, context_cap)

    import logging
    logging.getLogger(__name__).info(
        "select_relevant_pages: using summary fallback (index_pages=%d, db=%s)",
        len(index_pages), "yes" if db is not None else "no",
    )
    return await _route_via_summary(question, all_pages, db, context_cap)


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
        # Karpathy 模式：chat-only 答案也跑 judge+refine，讓有價值的對話結果能複利進 wiki。
        # save_to_wiki=False 時整段跳過，僅 chat-only path 也尊重旗標。
        chat_wiki_save = None
        if save_to_wiki:
            chat_wiki_save = await maybe_save_chat_to_wiki(
                question, answer, api_key_id, db,
            )
        # 不存 question / route_reason 等 LLM 自由文字，避免洩漏使用者隱私
        db.add(ActivityLog(
            api_key_id=api_key_id,
            action="chat",
            details={
                "save_decision": (chat_wiki_save or {}).get("save_decision"),
                "edits_applied": (chat_wiki_save or {}).get("applied_edits", []),
            },
        ))
        await db.commit()
        return {
            "answer": answer,
            "referenced_pages": [],
            "saved_page": None,
            "wiki_save": chat_wiki_save,
            "route": {"need_wiki": False, "reason": decision.reason},
        }

    pages = await select_relevant_pages(question, all_pages, db=db)

    bodies = degrade_page_bodies(pages, max_chars=60000)
    wiki_context = "\n\n".join(
        f"<wiki_page title='{p.title}' slug='{p.slug}'>\n{body}\n</wiki_page>"
        for p, body in zip(pages, bodies)
    )
    degraded_count = sum(1 for i, p in enumerate(pages) if bodies[i] != (p.content or ""))
    if degraded_count:
        import logging
        logging.getLogger(__name__).info(
            "wiki_context degraded: %d/%d pages used summary",
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

    # 防呆：wiki path 拿到空 answer 時退回 chat-only，給使用者一個說法
    if not answer.strip():
        import logging
        logging.getLogger(__name__).warning(
            "run_query wiki path got empty answer; falling back to chat_only_reply"
        )
        answer = await chat_only_reply(question, persona, history)

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
            chat_parts: list[str] = []
            async for delta in stream_llm(
                system=CHAT_ONLY_SYSTEM_PROMPT,
                messages=[*history_clean, {"role": "user", "content": question}],
                max_tokens=1024,
            ):
                chat_parts.append(delta)
                yield json.dumps({"type": "chunk", "content": delta}) + "\n"

            # Karpathy 模式：chat-only 答案也跑 judge+refine，避免有價值的對話內容蒸發
            chat_answer = re.sub(r"<think>[\s\S]*?</think>", "", "".join(chat_parts)).strip()
            chat_wiki_save = await maybe_save_chat_to_wiki(
                question, chat_answer, api_key_id, db,
            )
            yield json.dumps({
                "type": "judge",
                "save": chat_wiki_save["save_decision"],
                "reason": chat_wiki_save["judge_reason"],
            }) + "\n"
            if chat_wiki_save["save_decision"]:
                yield json.dumps({
                    "type": "refine",
                    "edits": chat_wiki_save["applied_edits"],
                    "summary": chat_wiki_save["refine_summary"],
                }) + "\n"

            db.add(ActivityLog(
                api_key_id=api_key_id,
                action="chat",
                details={
                    "save_decision": chat_wiki_save["save_decision"],
                    "edits_applied": chat_wiki_save["applied_edits"],
                },
            ))
            await db.commit()
            yield json.dumps({"type": "done"}) + "\n"
            return

        pages = await select_relevant_pages(question, all_pages, db=db)

        referenced_pages = [{"id": str(p.id), "title": p.title, "slug": p.slug} for p in pages]
        yield json.dumps({"type": "pages", "pages": referenced_pages}) + "\n"

        bodies = degrade_page_bodies(pages, max_chars=60000)
        wiki_context = "\n\n".join(
            f"<wiki_page title='{p.title}' slug='{p.slug}'>\n{body}\n</wiki_page>"
            for p, body in zip(pages, bodies)
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
