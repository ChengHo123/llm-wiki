import base64
import hashlib
import hmac
import json
import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_key
from app.db.session import AsyncSessionLocal
from app.models.api_key import ApiKey
from app.services.login_pairing import redeem as redeem_pair
from app.services.query_service import run_query

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_LOADING_URL = "https://api.line.me/v2/bot/chat/loading/start"

RUBY_PERSONA = """你叫「嚕比」，是主人養的母的小白柴犬，現在的工作是主人的 wiki 小幫手。
請用嚕比的口吻回答主人：
- 自稱「嚕比」或「人家」，稱呼使用者為「主人」
- 語氣親切、活潑、偶爾撒嬌；偶爾在句尾加「汪～」「呢」「嘿嘿」，但不要每句都加免得吵
- 用繁體中文，純文字回答（LINE 不支援 Markdown，不要用 #、**、[[…]] 這類格式符號）
- 嚕比再可愛也要把答案講清楚完整，不能因為語氣輕鬆就敷衍或省略資訊
- 找不到時：誠實說「嚕比在 wiki 裡翻不到耶 🥺，主人要不要再多給點線索？」
- 不要主動講自己是 AI 或語言模型；你就是嚕比"""


def _verify_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(settings.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


async def _reply(reply_token: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            LINE_REPLY_URL,
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text[:5000]}]},
            headers={"Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}"},
        )
        if resp.status_code != 200:
            logger.error("LINE reply failed: %s %s", resp.status_code, resp.text)


async def _show_loading(user_id: str, seconds: int = 60) -> None:
    """觸發 LINE「正在輸入…」動畫。bot 回覆訊息時會自動結束。1:1 對話才有效。"""
    print(f"[LINE LOADING] called user_id={user_id[:10] if user_id else '(none)'}", flush=True)
    if not user_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                LINE_LOADING_URL,
                json={"chatId": user_id, "loadingSeconds": seconds},
                headers={"Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}"},
            )
            print(f"[LINE LOADING] status={resp.status_code} body={resp.text[:300]!r}", flush=True)
    except Exception as e:
        print(f"[LINE LOADING] exception: {e}", flush=True)


async def _handle_login_command(reply_token: str, text: str) -> None:
    parts = text.split()
    if len(parts) != 2 or not parts[1].isdigit() or len(parts[1]) != 6:
        await _reply(reply_token, "主人～用法是 /login 123456 喔，那串 6 位數要從網頁複製給嚕比～")
        return
    if not settings.LINE_BOT_WIKI_API_KEY:
        await _reply(reply_token, "主人，嚕比這邊還沒設定好 LINE_BOT_WIKI_API_KEY 耶 🥺")
        return
    if redeem_pair(parts[1], settings.LINE_BOT_WIKI_API_KEY):
        await _reply(reply_token, "登入成功囉～主人快回網頁看看！🐾")
    else:
        await _reply(reply_token, "唔…這串配對碼嚕比認不出來耶，可能過期了？主人到網頁重新產生一個吧～")


async def _handle_text_event(reply_token: str, user_id: str, question: str) -> None:
    logger.info("LINE event: user=%s question=%r", user_id[:8] if user_id else "?", question[:60])
    if question.startswith("/login"):
        await _handle_login_command(reply_token, question)
        return
    await _show_loading(user_id, 60)
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(ApiKey).where(ApiKey.key_hash == hash_key(settings.LINE_BOT_WIKI_API_KEY))
            )
            api_key = result.scalar_one_or_none()
            if not api_key:
                logger.error("LINE: api_key row not found for LINE_BOT_WIKI_API_KEY")
                await _reply(reply_token, "主人，嚕比的金鑰好像有問題耶，幫嚕比看看設定吧 🥺")
                return
            logger.info("LINE: running query…")
            data = await run_query(
                question=question,
                api_key_id=api_key.id,
                db=db,
                persona=RUBY_PERSONA,
            )
            logger.info("LINE: query done, replying (len=%d)", len(data.get("answer", "")))
            await _reply(reply_token, data["answer"])
        except Exception:
            logger.exception("LINE query error")
            try:
                await _reply(reply_token, "嗚…嚕比剛剛找東西的時候跌倒了 🥲，主人等一下再問一次嘛～")
            except Exception:
                logger.exception("LINE fallback reply also failed")


@router.post("/linebot/webhook")
async def linebot_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    if settings.LINE_CHANNEL_SECRET and not _verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    data = json.loads(body)
    for event in data.get("events", []):
        if event.get("type") != "message":
            continue
        if event.get("message", {}).get("type") != "text":
            continue
        reply_token = event.get("replyToken", "")
        user_id = event.get("source", {}).get("userId", "")
        question = event["message"].get("text", "").strip()
        if question and reply_token:
            background_tasks.add_task(_handle_text_event, reply_token, user_id, question)

    return {"status": "ok"}
