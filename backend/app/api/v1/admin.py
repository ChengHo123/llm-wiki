import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.core.admin import (
    ADMIN_COOKIE_NAME,
    ADMIN_TOKEN_TTL,
    issue_admin_token,
    require_admin,
    verify_admin_token,
)
from app.core.config import get_settings
from app.db.session import get_db
from app.models.activity_log import ActivityLog
from app.models.api_key import ApiKey
from app.models.document import Document
from app.models.line_user_binding import LineUserBinding
from app.models.wiki_page import WikiPage
from app.services.ingest_queue import enqueue, request_cancel

router = APIRouter()


# ── Auth ────────────────────────────────────────────────


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/admin/login")
async def admin_login(body: LoginIn, response: Response):
    settings = get_settings()
    if body.username != settings.ADMIN_USERNAME or body.password != settings.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    token = issue_admin_token(body.username)
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        max_age=ADMIN_TOKEN_TTL,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True}


@router.post("/admin/logout")
async def admin_logout(response: Response, _: None = Depends(require_admin)):
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/admin/me")
async def admin_me(_: None = Depends(require_admin)):
    return {"ok": True}


from fastapi import Cookie as FastAPICookie
from fastapi.responses import RedirectResponse

@router.get("/admin/check")
async def admin_check(
    next: str = "/litellm/ui",
    admin_session: str | None = FastAPICookie(default=None, alias=ADMIN_COOKIE_NAME),
):
    """Caddy forward_auth 用：有效 cookie → 200；否則 302 到登入頁帶 next 參數。"""
    if admin_session and verify_admin_token(admin_session):
        return {"ok": True}
    safe_next = next if next.startswith("/") else "/admin/overview"
    return RedirectResponse(url=f"/admin/login?next={safe_next}", status_code=302)


# ── Overview（平台總覽）─────────────────────────────────


MAX_RANGE_DAYS = 366  # 防止用戶選太誇張的範圍把 trends 撐爆


def _parse_range(start_date: str | None, end_date: str | None) -> tuple[datetime, datetime, int]:
    """解析 ?start_date / ?end_date（YYYY-MM-DD，UTC）。
    預設：含今天往前 14 天。end 為當日 23:59:59，start 為當日 00:00。
    """
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else today
        start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else (end - timedelta(days=13))
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式需為 YYYY-MM-DD")
    if start > end:
        raise HTTPException(status_code=400, detail="start_date 不能晚於 end_date")
    days = (end - start).days + 1
    if days > MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail=f"日期範圍最大 {MAX_RANGE_DAYS} 天")
    # end 取當日結束（含當天）
    end_inclusive = end.replace(hour=23, minute=59, second=59, microsecond=999_999)
    return start, end_inclusive, days


class KpiOut(BaseModel):
    # 平台累積（不受範圍影響）
    total_users: int
    dau: int
    wau: int
    mau: int
    new_users_this_week: int
    new_users_last_week: int
    total_documents: int
    total_wiki_pages: int
    queue_depth: int
    # 範圍相依
    range_ingest_total: int
    range_ingest_done: int
    range_ingest_error: int
    range_success_rate: float | None
    range_query_count: int


class LeaderEntry(BaseModel):
    api_key_id: str
    name: str
    line_user_id: str | None
    value: int


class TrendPoint(BaseModel):
    date: str  # YYYY-MM-DD（UTC）
    ingest_done: int
    ingest_error: int
    ingest_total: int
    query_count: int


class RangeOut(BaseModel):
    start: str  # YYYY-MM-DD
    end: str
    days: int


class OverviewOut(BaseModel):
    range: RangeOut
    kpi: KpiOut
    top_uploaders: list[LeaderEntry]
    top_queriers: list[LeaderEntry]
    top_wiki: list[LeaderEntry]
    trends: list[TrendPoint]


@router.get("/admin/overview", response_model=OverviewOut)
async def overview(
    start_date: str | None = Query(None, description="YYYY-MM-DD（UTC）"),
    end_date: str | None = Query(None, description="YYYY-MM-DD（UTC）"),
    _: None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    range_start, range_end, range_days = _parse_range(start_date, end_date)

    now = datetime.utcnow()
    d1 = now - timedelta(days=1)
    d7 = now - timedelta(days=7)
    d14 = now - timedelta(days=14)
    d30 = now - timedelta(days=30)

    # ── Active users (DAU / WAU / MAU) ─────────────────────
    # 用 ActivityLog 的 distinct api_key_id 當作活躍指標
    dau = (await db.execute(
        select(func.count(func.distinct(ActivityLog.api_key_id)))
        .where(ActivityLog.created_at >= d1)
    )).scalar_one()
    wau = (await db.execute(
        select(func.count(func.distinct(ActivityLog.api_key_id)))
        .where(ActivityLog.created_at >= d7)
    )).scalar_one()
    mau = (await db.execute(
        select(func.count(func.distinct(ActivityLog.api_key_id)))
        .where(ActivityLog.created_at >= d30)
    )).scalar_one()

    # ── User counts ────────────────────────────────────────
    total_users = (await db.execute(select(func.count(ApiKey.id)))).scalar_one()
    new_this_week = (await db.execute(
        select(func.count(ApiKey.id)).where(ApiKey.created_at >= d7)
    )).scalar_one()
    new_last_week = (await db.execute(
        select(func.count(ApiKey.id)).where(
            ApiKey.created_at >= d14, ApiKey.created_at < d7
        )
    )).scalar_one()

    # ── Content totals ─────────────────────────────────────
    total_docs = (await db.execute(select(func.count(Document.id)))).scalar_one()
    total_wiki = (await db.execute(select(func.count(WikiPage.id)))).scalar_one()

    # ── 範圍內 ingest 狀況 ─────────────────────────────────
    range_done = (await db.execute(
        select(func.count(Document.id)).where(
            Document.created_at >= range_start,
            Document.created_at <= range_end,
            Document.status == "done",
        )
    )).scalar_one()
    range_error = (await db.execute(
        select(func.count(Document.id)).where(
            Document.created_at >= range_start,
            Document.created_at <= range_end,
            Document.status == "error",
        )
    )).scalar_one()
    range_total = (await db.execute(
        select(func.count(Document.id)).where(
            Document.created_at >= range_start,
            Document.created_at <= range_end,
        )
    )).scalar_one()
    range_finished = range_done + range_error
    range_success = (range_done / range_finished) if range_finished > 0 else None

    range_query = (await db.execute(
        select(func.count(ActivityLog.id)).where(
            ActivityLog.created_at >= range_start,
            ActivityLog.created_at <= range_end,
            ActivityLog.action.in_(["query", "chat"]),
        )
    )).scalar_one()

    queue_depth = (await db.execute(
        select(func.count(Document.id)).where(
            Document.status.in_(["queued", "processing"])
        )
    )).scalar_one()

    kpi = KpiOut(
        total_users=total_users,
        dau=dau,
        wau=wau,
        mau=mau,
        new_users_this_week=new_this_week,
        new_users_last_week=new_last_week,
        total_documents=total_docs,
        total_wiki_pages=total_wiki,
        queue_depth=queue_depth,
        range_ingest_total=range_total,
        range_ingest_done=range_done,
        range_ingest_error=range_error,
        range_success_rate=range_success,
        range_query_count=range_query,
    )

    # ── Leaderboards ──────────────────────────────────────
    bindings = (await db.execute(select(LineUserBinding))).scalars().all()
    line_map = {b.api_key_id: b.line_user_id for b in bindings}
    keys = (await db.execute(select(ApiKey))).scalars().all()
    name_map = {k.id: k.name for k in keys}

    def build_leader(rows: list[tuple[uuid.UUID, int]]) -> list[LeaderEntry]:
        return [
            LeaderEntry(
                api_key_id=str(kid),
                name=name_map.get(kid, "(未知)"),
                line_user_id=line_map.get(kid),
                value=int(value),
            )
            for kid, value in rows
            if kid in name_map  # 過濾掉孤兒紀錄
        ]

    top_uploaders_rows = (await db.execute(
        select(Document.api_key_id, func.count(Document.id).label("c"))
        .where(
            Document.created_at >= range_start,
            Document.created_at <= range_end,
        )
        .group_by(Document.api_key_id)
        .order_by(func.count(Document.id).desc())
        .limit(10)
    )).all()

    top_queriers_rows = (await db.execute(
        select(ActivityLog.api_key_id, func.count(ActivityLog.id).label("c"))
        .where(
            ActivityLog.action.in_(["query", "chat"]),
            ActivityLog.created_at >= range_start,
            ActivityLog.created_at <= range_end,
        )
        .group_by(ActivityLog.api_key_id)
        .order_by(func.count(ActivityLog.id).desc())
        .limit(10)
    )).all()

    top_wiki_rows = (await db.execute(
        select(WikiPage.api_key_id, func.count(WikiPage.id).label("c"))
        .where(
            WikiPage.created_at >= range_start,
            WikiPage.created_at <= range_end,
        )
        .group_by(WikiPage.api_key_id)
        .order_by(func.count(WikiPage.id).desc())
        .limit(10)
    )).all()

    # ── Trends（依範圍每日 bucket）─────────────────────────
    doc_day = func.date(Document.created_at)
    log_day = func.date(ActivityLog.created_at)

    ingest_rows = (await db.execute(
        select(
            doc_day.label("d"),
            Document.status,
            func.count(Document.id),
        )
        .where(
            Document.created_at >= range_start,
            Document.created_at <= range_end,
        )
        .group_by(doc_day, Document.status)
    )).all()

    query_rows = (await db.execute(
        select(log_day.label("d"), func.count(ActivityLog.id))
        .where(
            ActivityLog.created_at >= range_start,
            ActivityLog.created_at <= range_end,
            ActivityLog.action.in_(["query", "chat"]),
        )
        .group_by(log_day)
    )).all()

    # 把 row 攤成 dict 方便 lookup
    ingest_by_day: dict[str, dict[str, int]] = {}
    for d, status, count in ingest_rows:
        key = d.isoformat() if hasattr(d, "isoformat") else str(d)
        ingest_by_day.setdefault(key, {})[status] = int(count)

    query_by_day: dict[str, int] = {
        (d.isoformat() if hasattr(d, "isoformat") else str(d)): int(c)
        for d, c in query_rows
    }

    trends: list[TrendPoint] = []
    for i in range(range_days):
        day = range_start + timedelta(days=i)
        key = day.date().isoformat()
        bucket = ingest_by_day.get(key, {})
        done = bucket.get("done", 0)
        error = bucket.get("error", 0)
        # queued/processing 也算 total，但不算 done/error
        total = sum(bucket.values())
        trends.append(TrendPoint(
            date=key,
            ingest_done=done,
            ingest_error=error,
            ingest_total=total,
            query_count=query_by_day.get(key, 0),
        ))

    return OverviewOut(
        range=RangeOut(
            start=range_start.date().isoformat(),
            end=range_end.date().isoformat(),
            days=range_days,
        ),
        kpi=kpi,
        top_uploaders=build_leader(top_uploaders_rows),
        top_queriers=build_leader(top_queriers_rows),
        top_wiki=build_leader(top_wiki_rows),
        trends=trends,
    )


# ── Users ───────────────────────────────────────────────


class UserSummary(BaseModel):
    api_key_id: str
    name: str
    line_user_id: str | None
    end_user_tag: str  # LiteLLM /End-Users 頁面的 user 字串
    created_at: str
    document_count: int
    wiki_page_count: int
    in_progress_count: int
    chat_count: int


@router.get("/admin/users", response_model=list[UserSummary])
async def list_users(
    _: None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    api_keys = (
        await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    ).scalars().all()

    bindings = (await db.execute(select(LineUserBinding))).scalars().all()
    line_map = {b.api_key_id: b.line_user_id for b in bindings}

    out: list[UserSummary] = []
    for k in api_keys:
        doc_count = (
            await db.execute(
                select(func.count(Document.id)).where(Document.api_key_id == k.id)
            )
        ).scalar_one()
        wiki_count = (
            await db.execute(
                select(func.count(WikiPage.id)).where(WikiPage.api_key_id == k.id)
            )
        ).scalar_one()
        in_progress = (
            await db.execute(
                select(func.count(Document.id)).where(
                    Document.api_key_id == k.id,
                    Document.status.in_(["queued", "processing"]),
                )
            )
        ).scalar_one()
        chat_count = (
            await db.execute(
                select(func.count(ActivityLog.id)).where(
                    ActivityLog.api_key_id == k.id,
                    ActivityLog.action.in_(["query", "chat"]),
                )
            )
        ).scalar_one()
        line_id = line_map.get(k.id)
        out.append(
            UserSummary(
                api_key_id=str(k.id),
                name=k.name,
                line_user_id=line_id,
                end_user_tag=f"line-{line_id}" if line_id else f"web-{k.id}",
                created_at=k.created_at.isoformat(),
                document_count=doc_count,
                wiki_page_count=wiki_count,
                in_progress_count=in_progress,
                chat_count=chat_count,
            )
        )
    return out


class AdminDocumentOut(BaseModel):
    id: str
    filename: str
    content_type: str
    status: str
    error_message: str | None
    created_at: str


class UserDetail(BaseModel):
    summary: UserSummary
    documents: list[AdminDocumentOut]


@router.get("/admin/users/{api_key_id}", response_model=UserDetail)
async def user_detail(
    api_key_id: uuid.UUID,
    _: None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    api_key = (
        await db.execute(select(ApiKey).where(ApiKey.id == api_key_id))
    ).scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=404, detail="使用者不存在")

    binding = (
        await db.execute(
            select(LineUserBinding).where(LineUserBinding.api_key_id == api_key_id)
        )
    ).scalar_one_or_none()
    line_id = binding.line_user_id if binding else None

    docs = (
        await db.execute(
            select(Document)
            .where(Document.api_key_id == api_key_id)
            .order_by(Document.created_at.desc())
        )
    ).scalars().all()
    in_progress = sum(1 for d in docs if d.status in ("queued", "processing"))

    wiki_count = (
        await db.execute(
            select(func.count(WikiPage.id)).where(WikiPage.api_key_id == api_key_id)
        )
    ).scalar_one()
    chat_count = (
        await db.execute(
            select(func.count(ActivityLog.id)).where(
                ActivityLog.api_key_id == api_key_id,
                ActivityLog.action.in_(["query", "chat"]),
            )
        )
    ).scalar_one()

    return UserDetail(
        summary=UserSummary(
            api_key_id=str(api_key.id),
            name=api_key.name,
            line_user_id=line_id,
            end_user_tag=f"line-{line_id}" if line_id else f"web-{api_key.id}",
            created_at=api_key.created_at.isoformat(),
            document_count=len(docs),
            wiki_page_count=wiki_count,
            in_progress_count=in_progress,
            chat_count=chat_count,
        ),
        documents=[
            AdminDocumentOut(
                id=str(d.id),
                filename=d.filename,
                content_type=d.content_type,
                status=d.status,
                error_message=d.error_message,
                created_at=d.created_at.isoformat(),
            )
            for d in docs
        ],
    )


# ── Document control（admin 跨 user 重試 / 停止）─────────


@router.post("/admin/documents/{document_id}/retry")
async def admin_retry(
    document_id: uuid.UUID,
    _: None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    doc = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文件不存在")
    if doc.status == "processing":
        raise HTTPException(status_code=409, detail="文件處理中，請先停止")
    doc.status = "queued"
    doc.error_message = None
    await db.commit()
    await enqueue(doc.id)
    return {"ok": True}


@router.post("/admin/documents/{document_id}/stop")
async def admin_stop(
    document_id: uuid.UUID,
    _: None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    doc = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文件不存在")
    if doc.status not in ("queued", "processing"):
        raise HTTPException(status_code=409, detail="文件不在進行中")

    # processing 中：標記 cancellation，run_ingest 會在下個 chunk 邊界 bail
    # queued 但還沒被 worker 拿走：worker 拿走時看到 cancellation 旗標就 skip
    request_cancel(doc.id)
    if doc.status == "queued":
        # 馬上把狀態落地，不用等 worker 處理
        doc.status = "error"
        doc.error_message = "Admin 手動停止（排隊中）"
        await db.commit()
    return {"ok": True}


# ── Spend / Token（從 LiteLLM /spend/logs 拉資料）─────


class SpendUserEntry(BaseModel):
    api_key_id: str | None  # 對不到我們系統的 user 時為 None
    name: str
    line_user_id: str | None
    end_user_tag: str
    call_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    spend_usd: float


class SpendByModelEntry(BaseModel):
    model: str
    call_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    spend_usd: float


class SpendOut(BaseModel):
    total_call_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_spend_usd: float
    untagged_call_count: int  # 沒帶 end_user 的（多半是早期紀錄）
    untagged_tokens: int
    by_user: list[SpendUserEntry]
    by_model: list[SpendByModelEntry]
    fetched_count: int  # 實際從 LiteLLM 拿到的 log 筆數
    note: str | None  # 提示訊息（例如資料截斷或對不到 user）


def _parse_litellm_base() -> str:
    """LLM_BASE_URL 像 http://litellm:4000/v1，去掉 /v1 拿到 admin API base。"""
    base = get_settings().LLM_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


class SpendOutWithRange(SpendOut):
    range: RangeOut


@router.get("/admin/spend", response_model=SpendOutWithRange)
async def spend(
    start_date: str | None = Query(None, description="YYYY-MM-DD（UTC）"),
    end_date: str | None = Query(None, description="YYYY-MM-DD（UTC）"),
    _: None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    range_start, range_end, range_days = _parse_range(start_date, end_date)
    settings = get_settings()
    base = _parse_litellm_base()
    headers = {"Authorization": f"Bearer {settings.LLM_API_KEY}"}

    # 抓所有 spend 紀錄；LiteLLM /spend/logs 帶 date 參數會回傳 aggregated 而非 raw，
    # 故拿全部後在 Python 端用 startTime 過濾範圍。
    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            resp = await cli.get(f"{base}/spend/logs", headers=headers)
            resp.raise_for_status()
            all_logs = resp.json()
    except Exception as e:
        logger.warning("Failed to fetch /spend/logs: %s", e)
        raise HTTPException(status_code=502, detail=f"LiteLLM 連線失敗：{e}")

    if not isinstance(all_logs, list):
        raise HTTPException(status_code=502, detail="LiteLLM 回傳格式異常")

    # 按範圍過濾（用 startTime ISO 字串開頭即可比對日期）
    range_start_iso = range_start.isoformat()
    range_end_iso = range_end.isoformat()
    logs = [
        r for r in all_logs
        if r.get("startTime") and range_start_iso <= r["startTime"] <= range_end_iso
    ]

    # ── 建立 end_user_tag → api_key_id 對應 ──
    bindings = (await db.execute(select(LineUserBinding))).scalars().all()
    line_to_key: dict[str, uuid.UUID] = {b.line_user_id: b.api_key_id for b in bindings}
    keys = (await db.execute(select(ApiKey))).scalars().all()
    name_map: dict[uuid.UUID, str] = {k.id: k.name for k in keys}
    line_map_rev: dict[uuid.UUID, str] = {b.api_key_id: b.line_user_id for b in bindings}

    def resolve_user(end_user: str) -> tuple[uuid.UUID | None, str, str | None]:
        """end_user_tag → (api_key_id, name, line_user_id)。對不到回 (None, "(未知)", None)。"""
        if not end_user:
            return None, "(未標記)", None
        if end_user.startswith("line-"):
            line_id = end_user[5:]
            kid = line_to_key.get(line_id)
            if kid:
                return kid, name_map.get(kid, "(已刪除用戶)"), line_id
            return None, f"line:{line_id[:10]}…", line_id
        if end_user.startswith("web-"):
            try:
                kid = uuid.UUID(end_user[4:])
                if kid in name_map:
                    return kid, name_map[kid], line_map_rev.get(kid)
                return None, "(已刪除用戶)", None
            except ValueError:
                return None, end_user, None
        return None, end_user, None

    # ── 聚合 ──
    by_user: dict[str, dict] = defaultdict(lambda: {
        "calls": 0, "in": 0, "out": 0, "tot": 0, "spend": 0.0,
    })
    by_model: dict[str, dict] = defaultdict(lambda: {
        "calls": 0, "in": 0, "out": 0, "tot": 0, "spend": 0.0,
    })
    total_calls = total_in = total_out = total_tot = 0
    total_spend = 0.0
    untagged_calls = untagged_tokens = 0

    for r in logs:
        eu = r.get("end_user") or ""
        prompt = int(r.get("prompt_tokens") or 0)
        comp = int(r.get("completion_tokens") or 0)
        tot = int(r.get("total_tokens") or 0)
        sp = float(r.get("spend") or 0.0)
        model = r.get("model") or "(unknown)"

        total_calls += 1
        total_in += prompt
        total_out += comp
        total_tot += tot
        total_spend += sp
        if not eu:
            untagged_calls += 1
            untagged_tokens += tot

        u = by_user[eu]
        u["calls"] += 1
        u["in"] += prompt
        u["out"] += comp
        u["tot"] += tot
        u["spend"] += sp

        m = by_model[model]
        m["calls"] += 1
        m["in"] += prompt
        m["out"] += comp
        m["tot"] += tot
        m["spend"] += sp

    user_entries: list[SpendUserEntry] = []
    for eu, agg in by_user.items():
        kid, name, line_id = resolve_user(eu)
        user_entries.append(SpendUserEntry(
            api_key_id=str(kid) if kid else None,
            name=name,
            line_user_id=line_id,
            end_user_tag=eu or "(無)",
            call_count=agg["calls"],
            prompt_tokens=agg["in"],
            completion_tokens=agg["out"],
            total_tokens=agg["tot"],
            spend_usd=agg["spend"],
        ))
    user_entries.sort(key=lambda x: x.total_tokens, reverse=True)

    model_entries = [
        SpendByModelEntry(
            model=m,
            call_count=agg["calls"],
            prompt_tokens=agg["in"],
            completion_tokens=agg["out"],
            total_tokens=agg["tot"],
            spend_usd=agg["spend"],
        )
        for m, agg in by_model.items()
    ]
    model_entries.sort(key=lambda x: x.total_tokens, reverse=True)

    note = None
    if untagged_calls > 0:
        note = (
            f"有 {untagged_calls} 筆未標記用戶（多半是 end_user contextvar 加入前的舊紀錄，"
            f"共 {untagged_tokens:,} tokens）"
        )

    return SpendOutWithRange(
        range=RangeOut(
            start=range_start.date().isoformat(),
            end=range_end.date().isoformat(),
            days=range_days,
        ),
        total_call_count=total_calls,
        total_prompt_tokens=total_in,
        total_completion_tokens=total_out,
        total_tokens=total_tot,
        total_spend_usd=total_spend,
        untagged_call_count=untagged_calls,
        untagged_tokens=untagged_tokens,
        by_user=user_entries,
        by_model=model_entries,
        fetched_count=len(logs),
        note=note,
    )
