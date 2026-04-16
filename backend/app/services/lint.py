import json
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.wiki_page import WikiPage
from app.models.wiki_link import WikiLink
from app.models.activity_log import ActivityLog
from app.services.llm import call_llm

LINT_SYSTEM_PROMPT = """你是一個知識庫品質審查員。
請分析提供的 wiki 頁面，找出以下問題並以 JSON 格式回傳報告：

{
  "issues": [
    {
      "type": "contradiction|stale|orphan|missing_link|incomplete",
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

    # 找出孤立頁面（無連結）
    page_ids = {p.id for p in pages}
    links_result = await db.execute(
        select(WikiLink).where(WikiLink.source_page_id.in_(page_ids))
    )
    linked_sources = {lnk.source_page_id for lnk in links_result.scalars()}

    links_in_result = await db.execute(
        select(WikiLink).where(WikiLink.target_page_id.in_(page_ids))
    )
    linked_targets = {lnk.target_page_id for lnk in links_in_result.scalars()}
    orphan_ids = page_ids - linked_sources - linked_targets

    # 建立 wiki 快照
    pages_summary = "\n\n".join(
        f"<page slug='{p.slug}' type='{p.page_type}' orphan='{p.id in orphan_ids}'>\n"
        f"Title: {p.title}\n\n{p.content[:800]}{'...' if len(p.content) > 800 else ''}\n</page>"
        for p in pages[:30]  # 最多送 30 頁給 Claude 分析
    )

    raw = await call_llm(
        system=LINT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"請分析以下 wiki 頁面：\n\n{pages_summary}"}],
        max_tokens=4096,
    )

    json_match = re.search(r"```json\s*([\s\S]+?)\s*```", raw)
    json_str = json_match.group(1) if json_match else raw
    report = json.loads(json_str)

    # 補充真實統計
    report.setdefault("stats", {})
    report["stats"]["total_pages"] = len(pages)
    report["stats"]["orphan_pages"] = len(orphan_ids)
    report["stats"]["issues_found"] = len(report.get("issues", []))

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
