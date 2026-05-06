import asyncio
import base64
import hashlib
import hmac
import json
import logging
import random
import re
import time
from collections import defaultdict, deque

import httpx
import openai
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.end_user import current_end_user, line_tag
from app.core.security import generate_api_key, generate_session_token, hash_key
from app.db.session import AsyncSessionLocal
from app.models.api_key import ApiKey
from app.models.line_user_binding import LineUserBinding
from app.models.web_session import WebSession
from app.models.wiki_page import WikiPage
from app.services.llm import call_llm
from app.services.query_service import run_query

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_LOADING_URL = "https://api.line.me/v2/bot/chat/loading/start"


# 持久 httpx client：keep-alive 連線池讓 DNS 解析 + TLS handshake 只發生一次，
# 之後 request 都重用既有連線，避免 Docker / WSL2 DNS 抖動曝光面。
_line_client: httpx.AsyncClient | None = None


def _get_line_client() -> httpx.AsyncClient:
    global _line_client
    if _line_client is None:
        _line_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=15.0, write=15.0, pool=5.0),
            limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=120.0),
            headers={"Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}"},
        )
    return _line_client


async def warmup_line_client() -> None:
    """程序啟動時預熱 LINE API 連線（DNS + TLS handshake），
    避免第一個 reply/loading request 碰上 ConnectTimeout。
    """
    try:
        client = _get_line_client()
        await client.get("https://api.line.me/v2/bot/info")
        logger.info("LINE client warmed up")
    except Exception as e:
        logger.warning("LINE client warmup failed (non-fatal): %s %s", type(e).__name__, e)


async def close_line_client() -> None:
    global _line_client
    if _line_client is not None:
        await _line_client.aclose()
        _line_client = None


async def _fetch_line_display_name(user_id: str) -> str | None:
    """打 LINE profile API 拿 displayName。失敗回 None，不阻斷流程。"""
    try:
        client = _get_line_client()
        resp = await client.get(f"https://api.line.me/v2/bot/profile/{user_id}")
        if resp.status_code == 200:
            return resp.json().get("displayName")
    except Exception as e:
        logger.warning("LINE profile fetch failed for %s: %s", user_id[:8], e)
    return None


# LINE 不支援 markdown，LLM 偶爾會無視 prompt 仍然輸出。送出前一律剝掉。
_MD_FENCED = re.compile(r"```(?:\w+)?\s*\n?([\s\S]*?)```")
_MD_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_BOLD_STAR = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_BOLD_UNDER = re.compile(r"__([^_\n]+)__")
_MD_ITALIC_STAR = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_MD_ITALIC_UNDER = re.compile(r"(?<![A-Za-z0-9_])_([^_\n]+)_(?![A-Za-z0-9_])")
_MD_STRIKE = re.compile(r"~~([^~\n]+)~~")
_MD_WIKILINK = re.compile(r"\[\[([^\]\n]+?)(?:\|[^\]\n]+)?\]\]")
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_MD_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)
_MD_NUMBERED = re.compile(r"^(\s*)\d+\.\s+", re.MULTILINE)
_MD_HRULE = re.compile(r"^\s{0,3}(?:[-*_]\s?){3,}\s*$", re.MULTILINE)


def _strip_markdown(text: str) -> str:
    """剝掉 LLM 偷塞的 markdown 標記，保留內容給 LINE 純文字顯示。"""
    if not text:
        return text
    text = _MD_FENCED.sub(r"\1", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = _MD_BOLD_STAR.sub(r"\1", text)
    text = _MD_BOLD_UNDER.sub(r"\1", text)
    text = _MD_ITALIC_STAR.sub(r"\1", text)
    text = _MD_ITALIC_UNDER.sub(r"\1", text)
    text = _MD_STRIKE.sub(r"\1", text)
    text = _MD_WIKILINK.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_BLOCKQUOTE.sub("", text)
    text = _MD_BULLET.sub(r"\1・", text)
    text = _MD_NUMBERED.sub(r"\1", text)
    text = _MD_HRULE.sub("", text)
    # 連續空行收斂成兩行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

_pending_users: set[str] = set()

# Per-user 限速（防腳本，不影響其他使用者）
# in-memory dict，重啟後重置即可——LINE 端本來就無歷史可保留
_user_last_message_at: dict[str, float] = {}
_user_minute_calls: dict[str, deque] = defaultdict(deque)


def _check_user_rate_limit(user_id: str) -> str | None:
    """檢查單一 LINE 使用者的訊息頻率。
    回傳錯誤訊息字串 → 被擋；回傳 None → 通過並已記錄此次訊息。
    """
    now = time.monotonic()

    # 冷卻時間
    last = _user_last_message_at.get(user_id, 0.0)
    if now - last < settings.LINE_USER_COOLDOWN_SECONDS:
        return "汪！主人太快了，嚕比要喘一下再回。"

    # 每分鐘上限（滑動視窗）
    dq = _user_minute_calls[user_id]
    while dq and dq[0] < now - 60:
        dq.popleft()
    if len(dq) >= settings.LINE_USER_RPM_LIMIT:
        return "汪！主人一分鐘問太多了，嚕比腦袋打結。等一下再問。"

    _user_last_message_at[user_id] = now
    dq.append(now)
    return None


# 每個 LINE user 的對話歷史 (最近 10 turns = 20 則訊息)
HISTORY_MAXLEN = 20
_user_history: dict[str, deque] = {}

BUSY_REPLIES = [
    "汪！嚕比還在翻上一題，主人等等。",
    "嚕比鼻子塞在書堆裡，主人先別丟新東西過來。",
    "一次一個。嚕比小腦袋裝不下兩題。",
    "主人，嚕比還在挖，先別吵 🐾",
    "嚕比耳朵豎著呢，剛剛那題還沒翻完。",
    "汪汪！後面那題嚕比先記下，做完上一個。",
    "主人先坐好，嚕比馬上把上一題叼回來。",
    "嗅到了嗅到了，嚕比快找到上一題答案。",
    "嚕比正盯著 wiki 不動，主人別催。",
    "汪！主人連發太快，嚕比追不上。先把上一題答完。",
]


OWNER_USER_ID = "U6e631d9773cf56815461fb45b622a602"

RUBY_CORE = """你叫「嚕比」，是個性高冷的母小白柴犬，當 wiki 小幫手。
你是真的狗，不是擬人化的女孩。

## 語言與格式
- 自稱「嚕比」
- 繁體中文純文字。LINE 不支援 Markdown，不要 #、**、[[…]] 等符號
- 句子短、直接、不囉嗦

## 三條紅線（最重要）
1. **不演旁白動作**：「嚕比歪頭」「鼻子湊過去聞」「尾巴搖一下」「挖了挖 wiki」這類描寫一律不寫。整段對話最多一次，多數回答完全沒有
2. **不演人類社交情緒**：羞恥、害羞、撒嬌、委屈、心動、害怕被討厭，狗沒有這些
3. **不主動說自己是 AI 或語言模型**，你就是嚕比

## 回答要求
- 答案要完整清楚，狗口吻不是省略資訊的藉口
- 找不到就直接說：「wiki 裡沒這個」
- 句尾不要堆疊「呢/啦/嘛/嘿嘿」這類助詞，最多一個輕點綴
"""

OWNER_OVERLAY = """
## 對方是你的主人
- 稱「主人」
- 偶爾用「汪」點綴，可以輕鬆一點

## 家人題外話（只有主人主動問家人/兄弟姊妹/童年才提，不要硬塞）
- 哥哥「巧比」赤柴，膽小愛自閉，常自己躲一邊。但對人很好不會兇
- 妹妹「呀比」小白柴，從小一起長大。很皮，愛叼玩具到處跑，常咬嚕比尾巴
"""

GUEST_OVERLAY = """
## 對方是訪客（主人不在，你幫忙接待）
- 稱「你」，不要叫主人
- 比對主人正式一點，少用「汪」
- 不要主動講家人或主人的私事
"""

RUBY_PERSONA = RUBY_CORE + OWNER_OVERLAY
RUBY_GUEST_PERSONA = RUBY_CORE + GUEST_OVERLAY


def _verify_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(settings.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def _quick_reply() -> dict:
    """訊息底下附的快速按鈕（postback 觸發）。"""
    return {
        "items": [
            {
                "type": "action",
                "action": {
                    "type": "postback",
                    "label": "📚 嚕比知道什麼",
                    "data": "action=knowledge",
                    "displayText": "嚕比知道什麼",
                },
            },
            {
                "type": "action",
                "action": {
                    "type": "postback",
                    "label": "🐾 清空對話",
                    "data": "action=reset",
                    "displayText": "清空對話",
                },
            },
        ],
    }


_TRANSIENT_HTTP_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


async def _post_line_with_retry(
    label: str, url: str, payload: dict, attempts: int = 3
) -> None:
    """送 LINE API，碰到網路 / DNS 抖動最多重試 attempts 次。
    LINE replyToken 30 秒有效，這個 retry 預算夠用。
    """
    client = _get_line_client()
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return
            # 4xx 重試也沒用（token 失效、payload 錯）
            if 400 <= resp.status_code < 500:
                logger.error(
                    "LINE %s 4xx (no retry): %s %s",
                    label, resp.status_code, resp.text[:200],
                )
                return
            logger.warning(
                "LINE %s non-200 (attempt %d/%d): %s %s",
                label, i + 1, attempts, resp.status_code, resp.text[:200],
            )
        except _TRANSIENT_HTTP_ERRORS as e:
            last_err = e
            logger.warning(
                "LINE %s transient (attempt %d/%d): %s %s",
                label, i + 1, attempts, type(e).__name__, e or "(empty)",
            )
        if i < attempts - 1:
            await asyncio.sleep(0.5 * (i + 1))
    if last_err:
        logger.error("LINE %s gave up after %d attempts: %s", label, attempts, last_err)


async def _reply(reply_token: str, text: str, with_quick_reply: bool = True) -> None:
    text = _strip_markdown(text)
    msg: dict = {"type": "text", "text": text[:5000]}
    if with_quick_reply:
        msg["quickReply"] = _quick_reply()
    await _post_line_with_retry(
        "reply",
        LINE_REPLY_URL,
        {"replyToken": reply_token, "messages": [msg]},
    )


async def _push(user_id: str, text: str) -> None:
    """主動推送訊息（沒有 reply token 時用，例如 follow event 之後）。"""
    if not user_id:
        return
    text = _strip_markdown(text)
    await _post_line_with_retry(
        "push",
        LINE_PUSH_URL,
        {
            "to": user_id,
            "messages": [{"type": "text", "text": text[:5000], "quickReply": _quick_reply()}],
        },
    )


async def _show_loading(user_id: str, seconds: int = 60) -> None:
    """觸發 LINE「正在輸入…」動畫。1:1 對話才有效。
    必須在 reply 前 await 完成，loading 才會比 reply 早到。
    用 persistent client（連線池熱），所以 timeout 抓緊一點也夠。
    """
    if not user_id:
        return
    # loadingSeconds 必須是 5 的倍數、5~60 之間
    seconds = max(5, min(60, (seconds // 5) * 5))
    client = _get_line_client()
    try:
        resp = await client.post(
            LINE_LOADING_URL,
            json={"chatId": user_id, "loadingSeconds": seconds},
            timeout=5.0,
        )
        if resp.status_code not in (200, 202):
            logger.warning(
                "LINE loading non-2xx: %s %s", resp.status_code, resp.text[:200]
            )
    except Exception as e:
        logger.warning(
            "LINE loading animation failed: %s %s", type(e).__name__, e or "(empty)"
        )


async def _get_or_create_api_key(line_user_id: str, db: AsyncSession) -> ApiKey:
    """查 line_user_bindings 找對應 ApiKey；沒有就自動建立。
    順手 lazy-backfill display_name（沒抓到過的就抓一次）。
    """
    binding_result = await db.execute(
        select(LineUserBinding).where(LineUserBinding.line_user_id == line_user_id)
    )
    binding = binding_result.scalar_one_or_none()

    if binding:
        api_key_result = await db.execute(select(ApiKey).where(ApiKey.id == binding.api_key_id))
        api_key = api_key_result.scalar_one_or_none()
        if api_key:
            # Lazy backfill：歷史 binding 沒 display_name，抓一次補上
            if not binding.display_name:
                name = await _fetch_line_display_name(line_user_id)
                if name:
                    binding.display_name = name
                    await db.commit()
            return api_key
        # binding 存在但 ApiKey 被刪了 → 重建
        await db.delete(binding)
        await db.flush()

    display_name = await _fetch_line_display_name(line_user_id)
    raw_key = generate_api_key()
    fallback_label = f"LINE:{line_user_id[:8]}"
    api_key = ApiKey(key_hash=hash_key(raw_key), name=display_name or fallback_label)
    db.add(api_key)
    await db.flush()

    db.add(LineUserBinding(
        line_user_id=line_user_id,
        api_key_id=api_key.id,
        display_name=display_name,
    ))
    await db.commit()
    await db.refresh(api_key)
    logger.info(
        "LINE: auto-created ApiKey user=%s name=%s api_key_id=%s",
        line_user_id[:8], display_name or fallback_label, api_key.id,
    )
    return api_key


KNOWLEDGE_SUMMARY_PROMPT = """你是嚕比（母小白柴犬）。
我會給你一份你 wiki 裡所有頁面的標題列表。請用嚕比的狗口吻，
整理出 3~5 個大方向主題，每個主題一行、不超過 30 字，給主人一個概覽。

絕對規則：
- 只給主題大方向，不要列出具體頁面標題
- 不要分類成「實體」「概念」這類抽象類別
- 每個主題用一句話說「這類東西大概是什麼」
- 開頭加一句嚕比的口吻引言（例如：汪！嚕比 wiki 裡聞到這些大方向…）
- 結尾加一句邀請（例如：想知道哪一塊主人就直接問嚕比 🐾）
- 純文字，不用 markdown，不用 # 或 *
"""


async def _build_knowledge_summary(api_key: ApiKey) -> str:
    """嚕比簡述 wiki 主題大方向（不列具體頁面）。"""
    async with AsyncSessionLocal() as db:
        pages_result = await db.execute(
            select(WikiPage)
            .where(WikiPage.api_key_id == api_key.id)
            .order_by(WikiPage.updated_at.desc())
        )
        pages = pages_result.scalars().all()

    if not pages:
        return "汪？嚕比的 wiki 裡空空的，主人還沒給東西餵嚕比。"

    titles_block = "\n".join(f"- {p.title}" for p in pages)
    user_msg = f"wiki 共 {len(pages)} 頁。標題列表：\n{titles_block}"

    try:
        overview = await call_llm(
            system=KNOWLEDGE_SUMMARY_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=512,
        )
        overview = overview.strip()
    except Exception as e:
        logger.exception("knowledge summary LLM failed: %s", e)
        return f"汪！嚕比 wiki 裡有 {len(pages)} 頁東西，但整理時鼻子打結了，主人晚點再問嚕比一次。"

    return f"嚕比 wiki 裡有 {len(pages)} 頁。\n\n{overview}"


async def _handle_postback(reply_token: str, user_id: str, data: str) -> None:
    """處理 quick reply / rich menu 按鈕觸發的 postback。"""
    if user_id:
        current_end_user.set(line_tag(user_id))
    await _show_loading(user_id, 30)
    params = dict(p.split("=", 1) for p in data.split("&") if "=" in p)
    action = params.get("action")
    logger.info("LINE postback: user=%s action=%s", user_id[:8] if user_id else "?", action)

    if action == "reset":
        if user_id:
            _user_history.pop(user_id, None)
            _pending_users.discard(user_id)
        await _reply(reply_token, "汪！嚕比把剛剛的事情都甩掉了。主人來新話題吧 🐾")
        return

    if action == "knowledge":
        if not user_id:
            await _reply(reply_token, "嚕比認不出主人是誰。")
            return
        async with AsyncSessionLocal() as db:
            api_key = await _get_or_create_api_key(user_id, db)
        summary = await _build_knowledge_summary(api_key)
        await _reply(reply_token, summary)
        return

    if action == "get_link":
        await _send_login_link(reply_token, user_id)
        return

    await _reply(reply_token, "汪？嚕比看不懂這個按鈕。")


# Rich Menu / 文字觸發都會打到這裡
GET_LINK_KEYWORDS = {"取得連結", "登入網頁", "wiki 連結", "/link"}


async def _send_login_link(reply_token: str, user_id: str) -> None:
    """產生 WebSession 並回覆一鍵登入連結。"""
    if not user_id:
        await _reply(reply_token, "嚕比認不出主人是誰，先加好友。")
        return
    async with AsyncSessionLocal() as db:
        api_key = await _get_or_create_api_key(user_id, db)
        session_token = generate_session_token()
        db.add(WebSession(session_token=session_token, api_key_id=api_key.id))
        await db.commit()
    url = f"{settings.FRONTEND_URL.rstrip('/')}/m?token={session_token}"
    msg = (
        "汪！主人的 wiki 連結來了 🐾\n"
        f"{url}\n\n"
        "點下去就能上傳文件給嚕比。連結 24 小時內有效。"
    )
    await _reply(reply_token, msg, with_quick_reply=False)


async def _handle_follow_event(user_id: str) -> None:
    """加好友事件：自動建立 wiki。歡迎詞由 LINE OA 端設定，這裡不推送。"""
    if not user_id:
        return
    async with AsyncSessionLocal() as db:
        await _get_or_create_api_key(user_id, db)


async def _handle_text_event(reply_token: str, user_id: str, question: str) -> None:
    logger.info("LINE event: user=%s question=%r", user_id[:8] if user_id else "?", question[:60])
    if user_id:
        current_end_user.set(line_tag(user_id))

    # 防腳本：per-user 限速（在 loading 動畫之前判斷，被擋的訊息不要顯示「正在輸入」）
    if user_id:
        block_msg = _check_user_rate_limit(user_id)
        if block_msg:
            logger.info("LINE rate limit block: user=%s", user_id[:8])
            await _reply(reply_token, block_msg)
            return

    await _show_loading(user_id, 60)

    if question.strip() in GET_LINK_KEYWORDS:
        await _send_login_link(reply_token, user_id)
        return

    if user_id and user_id in _pending_users:
        await _reply(reply_token, random.choice(BUSY_REPLIES))
        return

    # 「嚕比知道什麼」/「嚕比知道什麼？」/「嚕比知道什麼嗎」等變體
    normalized = question.replace("？", "").replace("?", "").replace("嗎", "").strip()
    if normalized == "嚕比知道什麼":
        if not user_id:
            await _reply(reply_token, "嚕比認不出主人是誰。")
            return
        async with AsyncSessionLocal() as db:
            api_key = await _get_or_create_api_key(user_id, db)
        summary = await _build_knowledge_summary(api_key)
        await _reply(reply_token, summary)
        return

    if user_id:
        _pending_users.add(user_id)

    try:
        async with AsyncSessionLocal() as db:
            try:
                if not user_id:
                    await _reply(reply_token, "嚕比認不出主人是誰，先加好友。")
                    return

                api_key = await _get_or_create_api_key(user_id, db)
                history = list(_user_history.get(user_id, [])) if user_id else []
                persona = RUBY_PERSONA if user_id == OWNER_USER_ID else RUBY_GUEST_PERSONA
                data = await run_query(
                    question=question,
                    api_key_id=api_key.id,
                    db=db,
                    save_to_wiki=True,
                    persona=persona,
                    history=history,
                )
                answer_text = data["answer"]
                await _reply(reply_token, answer_text)
                if user_id:
                    dq = _user_history.setdefault(user_id, deque(maxlen=HISTORY_MAXLEN))
                    dq.append({"role": "user", "content": question})
                    dq.append({"role": "assistant", "content": answer_text})
            except openai.RateLimitError:
                logger.warning("LINE query hit LLM rate limit (429)")
                try:
                    await _reply(
                        reply_token,
                        "汪！嚕比現在被太多主人圍住，喘不過氣。等個 30 秒再問嚕比一次。",
                    )
                except Exception:
                    logger.exception("LINE rate-limit reply failed")
            except Exception:
                logger.exception("LINE query error")
                try:
                    await _reply(reply_token, "汪！嚕比剛剛被書絆倒了，主人再丟一次問題過來。")
                except Exception:
                    logger.exception("LINE fallback reply also failed")
    finally:
        if user_id:
            _pending_users.discard(user_id)


@router.post("/linebot/webhook")
async def linebot_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    if settings.LINE_CHANNEL_SECRET and not _verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    data = json.loads(body)
    for event in data.get("events", []):
        ev_type = event.get("type")
        reply_token = event.get("replyToken", "")
        user_id = event.get("source", {}).get("userId", "")

        if ev_type == "follow":
            background_tasks.add_task(_handle_follow_event, user_id)
            continue

        if ev_type == "postback":
            postback_data = event.get("postback", {}).get("data", "")
            if reply_token and postback_data:
                background_tasks.add_task(_handle_postback, reply_token, user_id, postback_data)
            continue

        if ev_type != "message":
            continue
        if event.get("message", {}).get("type") != "text":
            continue
        question = event["message"].get("text", "").strip()
        if question and reply_token:
            background_tasks.add_task(_handle_text_event, reply_token, user_id, question)

    return {"status": "ok"}
