import json
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.wiki_page import WikiPage
from app.models.wiki_link import WikiLink
from app.models.activity_log import ActivityLog
from app.services.llm import call_llm
from app.services.wiki_links import reconcile_wiki_links_from_content, parse_wikilinks

APPLY_SYSTEM_PROMPT = """你是 wiki 編輯助手。
基於使用者提供的 issues（每個含 description 與 suggestion），改寫下方 wiki 頁面內容以解決所有 issues。

規則：
- 保留原頁面的整體結構、語氣、既有正確資訊
- 僅做必要的新增、補充、修正、加連結
- wiki 內部連結使用 [[slug|顯示文字]] 或 [[slug]] 格式
- 回傳「改寫後的完整 markdown 內容」，不要加 code fence、不要加前後說明文字、不要加 <think>
"""


LINT_SYSTEM_PROMPT = """你是一個知識庫品質審查員。
請分析提供的 wiki 頁面，找出以下問題並以 JSON 格式回傳報告：

{
  "issues": [
    {
      "type": "contradiction|stale|orphan|missing_link|incomplete|gap",
      "severity": "high|medium|low",
      "page_slug": "相關頁面的 slug",
      "description": "問題描述",
      "suggestion": "建議修正方式"
    }
  ],
  "stats": {
    "total_pages": 數字,
    "orphan_pages": 數字,
    "issues_found": 數字
  },
  "summary": "整體健康狀況摘要"
}

issue types：
- contradiction: 頁面間內容矛盾
- stale: 內容可能已過時或不完整
- orphan: 沒有任何連入或連出連結的頁面
- missing_link: 內容提到某概念但缺少對應連結
- incomplete: 頁面內容過於簡短或缺乏實質內容
- gap: wiki 該涵蓋但缺整頁的主題（多頁不斷提到某概念/實體但沒人為它建頁；或結構上應有的入口/章節空缺）

注意：輸入中的頁面內容可能已被系統預覽截斷（結尾會有 "..."），這是顯示用的截斷，
不代表該頁面實際資料不完整。只有當「截斷前的可見內容」本身就稀薄、只有標題或一兩行、
或明顯缺乏實質資訊時才標為 incomplete。不要僅因為結尾有 "..." 就判為 incomplete。
"""


async def run_lint(api_key_id: uuid.UUID, db: AsyncSession) -> dict:
    """執行 wiki 健檢"""
    result = await db.execute(
        select(WikiPage)
        .where(WikiPage.api_key_id == api_key_id)
        .order_by(WikiPage.updated_at.desc())
    )
    pages = result.scalars().all()

    if not pages:
        return {
            "issues": [],
            "stats": {"total_pages": 0, "orphan_pages": 0, "issues_found": 0},
            "summary": "Wiki 目前沒有任何頁面",
        }

    # 找出孤立頁面（無真實語意連結）
    # 排除 ingest 自動兜底產生的 doc-index 連結：每份文件都會自動生 doc-index-*
    # 指向自己貢獻的所有頁，若把這當「有人連到」算入 incoming，所有頁面幾乎永遠非 orphan，
    # Lint 的孤立頁偵測會直接失能。orphan 應該反映「語意上沒人連過來」，
    # 制度性 glue 不算數。
    page_ids = {p.id for p in pages}
    doc_index_ids = {p.id for p in pages if p.slug.startswith("doc-index-")}

    links_result = await db.execute(
        select(WikiLink).where(WikiLink.source_page_id.in_(page_ids))
    )
    linked_sources = {lnk.source_page_id for lnk in links_result.scalars()}

    links_in_result = await db.execute(
        select(WikiLink).where(WikiLink.target_page_id.in_(page_ids))
    )
    linked_targets = {
        lnk.target_page_id
        for lnk in links_in_result.scalars()
        if lnk.source_page_id not in doc_index_ids
    }
    orphan_ids = page_ids - linked_sources - linked_targets

    # Code-side gap 偵測：掃所有頁面 [[wikilink]]，目標不存在於 wiki 的就是 gap。
    # 對齊 Karpathy Lint「資料缺口」概念——LLM 寫 [[X]] 卻沒有 X 頁面，要嘛該補要嘛該修。
    # 一個 gap target 只報一次（以第一個提及它的頁面當 page_slug），避免重複噪音。
    known_keys = {p.slug for p in pages} | {p.title for p in pages}
    gap_issues: list[dict] = []
    seen_gaps: set[str] = set()
    for p in pages:
        for tgt in parse_wikilinks(p.content or ""):
            if tgt in known_keys or tgt in seen_gaps:
                continue
            seen_gaps.add(tgt)
            gap_issues.append({
                "type": "gap",
                "severity": "medium",
                "page_slug": p.slug,
                "description": f"頁面引用 [[{tgt}]] 但 wiki 中無對應頁面",
                "suggestion": f"新建 '{tgt}' 頁面補上此主題，或將連結改為既有 slug/title",
            })

    # 建立 wiki 快照：每頁附真實長度，避免 LLM 因 "..." 誤判 incomplete
    SNIPPET_LEN = 2000
    BATCH_SIZE = 30

    def _page_block(p) -> str:
        suffix = "...（顯示截斷，實際長度見 full_length）" if len(p.content) > SNIPPET_LEN else ""
        return (
            f"<page slug='{p.slug}' type='{p.page_type}' orphan='{p.id in orphan_ids}' "
            f"full_length='{len(p.content)}'>\n"
            f"Title: {p.title}\n\n"
            f"{p.content[:SNIPPET_LEN]}{suffix}\n</page>"
        )

    all_issues: list[dict] = []
    summary_parts: list[str] = []
    parse_failures = 0
    for batch_idx in range(0, len(pages), BATCH_SIZE):
        batch = pages[batch_idx : batch_idx + BATCH_SIZE]
        pages_summary = "\n\n".join(_page_block(p) for p in batch)
        try:
            raw = await call_llm(
                system=LINT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"請分析以下 wiki 頁面：\n\n{pages_summary}"}],
                max_tokens=8192,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception(
                "lint LLM call failed on batch %d: %s", batch_idx, e
            )
            parse_failures += 1
            continue

        json_match = re.search(r"```json\s*([\s\S]+?)\s*```", raw)
        json_str = json_match.group(1) if json_match else raw
        try:
            batch_report = json.loads(json_str)
        except json.JSONDecodeError as e:
            import logging
            logging.getLogger(__name__).warning(
                "lint JSON parse failed on batch %d: %s; raw preview: %s",
                batch_idx, e, raw[:300],
            )
            parse_failures += 1
            continue

        all_issues.extend(batch_report.get("issues", []) or [])
        if batch_report.get("summary"):
            summary_parts.append(str(batch_report["summary"]))

    # Code-side gap issues 排前面，方便使用者先處理確定性的資料缺口
    merged_issues = gap_issues + all_issues
    report = {
        "issues": merged_issues,
        "summary": "\n\n".join(summary_parts) if summary_parts else "（無摘要）",
        "stats": {
            "total_pages": len(pages),
            "orphan_pages": len(orphan_ids),
            "issues_found": len(merged_issues),
            "gaps_found": len(gap_issues),
            "batches": (len(pages) + BATCH_SIZE - 1) // BATCH_SIZE,
            "batch_parse_failures": parse_failures,
        },
    }

    db.add(ActivityLog(
        api_key_id=api_key_id,
        action="lint",
        details={
            "total_pages": len(pages),
            "issues_found": report["stats"]["issues_found"],
        },
    ))
    await db.commit()

    return report


async def apply_lint_fixes(
    api_key_id: uuid.UUID,
    issues: list[dict],
    db: AsyncSession,
) -> dict:
    """依 issues[] 改寫對應 wiki page。每頁一次 LLM 呼叫，合併該頁所有 issue。"""
    if not issues:
        return {"applied": [], "skipped": []}

    grouped: dict[str, list[dict]] = {}
    for issue in issues:
        slug = issue.get("page_slug")
        if not slug:
            continue
        grouped.setdefault(slug, []).append(issue)

    applied: list[dict] = []
    skipped: list[dict] = []

    for slug, slug_issues in grouped.items():
        page_result = await db.execute(
            select(WikiPage).where(
                WikiPage.api_key_id == api_key_id,
                WikiPage.slug == slug,
            )
        )
        page = page_result.scalar_one_or_none()
        if not page:
            skipped.append({"page_slug": slug, "reason": "頁面不存在"})
            continue

        issue_block = "\n".join(
            f"- [{i.get('type', 'issue')}] {i.get('description', '')}\n  建議：{i.get('suggestion', '')}"
            for i in slug_issues
        )
        user_content = (
            f"頁面 slug：{page.slug}\n"
            f"頁面標題：{page.title}\n\n"
            f"Issues：\n{issue_block}\n\n"
            f"目前頁面內容：\n---\n{page.content}\n---"
        )

        try:
            raw = await call_llm(
                system=APPLY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=8192,
            )
        except Exception as e:
            skipped.append({"page_slug": slug, "reason": f"LLM 失敗：{e}"})
            continue

        new_content = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE).strip()
        fence = re.match(r"^```(?:markdown)?\s*\n([\s\S]*?)\n```\s*$", new_content)
        if fence:
            new_content = fence.group(1).strip()

        if not new_content:
            skipped.append({"page_slug": slug, "reason": "LLM 回傳空內容"})
            continue

        page.content = new_content
        # content 改了 → summary 失效；清空讓 backfill 路徑或下次 ingest 重產
        # （避免在 lint 流程裡多花一次 LLM 呼叫去同步 summary）
        page.summary = ""
        # content 改了 → outgoing wiki_links 失效；用 content 中的 [[...]] 重建邊，
        # 確保圖譜、index 路由的鄰居展開都看到最新連結
        link_count = await reconcile_wiki_links_from_content(db, page, api_key_id)
        applied.append({
            "page_slug": slug,
            "page_id": str(page.id),
            "title": page.title,
            "issues_addressed": len(slug_issues),
            "links_rebuilt": link_count,
        })

    db.add(ActivityLog(
        api_key_id=api_key_id,
        action="lint_apply",
        details={
            "requested": len(issues),
            "applied_pages": len(applied),
            "skipped": len(skipped),
        },
    ))
    await db.commit()

    return {"applied": applied, "skipped": skipped}
