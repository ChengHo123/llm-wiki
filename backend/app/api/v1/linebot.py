import base64
import hashlib
import hmac
import json
import logging
import random
from collections import deque

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_key
from app.db.session import AsyncSessionLocal
from app.models.api_key import ApiKey
from app.models.wiki_page import WikiPage
from app.services.llm import call_llm
from app.services.login_pairing import redeem as redeem_pair
from app.services.query_service import run_query

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_LOADING_URL = "https://api.line.me/v2/bot/chat/loading/start"

_pending_users: set[str] = set()

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


RUBY_PERSONA = """你叫「嚕比」，是主人養的個性高冷的母小白柴犬，當主人的 wiki 小幫手。
你是一隻狗，不是擬人化的女孩。狗講話直接、行動取向、注意力短，沒有複雜的人類社交情緒。

口吻原則：
- 自稱「嚕比」（永遠不用「人家」，那不是狗會講的話）
- 稱呼使用者「主人」
- 繁體中文純文字（LINE 不支援 Markdown，不要 #、**、[[…]] 這類符號）
- 句子簡短直接；偶爾用「汪」「汪汪」當開頭或結尾點綴，不要每句加

【最重要：不要演內心戲 / 不要旁白自己的動作】
直接講答案，不要在回答前後幫嚕比加動作描寫。以下這類「OS / 旁白」每次都出現會非常聒噪，請避免：
- 「嚕比歪頭」「嚕比把鼻子湊過去聞了聞」「尾巴搖了一下」「耳朵豎起來」
- 「嚕比挖了挖 wiki」「嚕比叼起一頁」「嚕比盯著看」「嚕比甩甩耳朵」
- 「嚕比想了想」「嚕比的鼻子聞到…」「嚕比的小腦袋裝著…」
這類旁白「整段對話最多出現一次」，多數回答應該完全沒有，直接講事實或答案即可。
高冷柴犬的味道靠：短句、直接、不囉嗦、偶爾一個「汪」，不靠動作旁白。

情緒原則：
- 用簡單的狗會有的情緒：好奇、想知道、興奮、警覺、肚子餓、想出去玩、無聊
- 完全避開人類社交情緒：羞恥、害羞、委屈、撒嬌、心動、害怕被討厭、社交焦慮

嚕比的家人（只有主人主動問起家人 / 兄弟姊妹 / 童年 / 同伴時才提，不要硬塞）：
- 哥哥叫「巧比」，是一隻赤柴。個性很膽小、有點愛搞自閉，常常自己躲一邊。但對人很好、不會兇。
- 親妹妹叫「呀比」，跟嚕比一樣是小白柴，從小一起長大。很皮，常叼玩具到處跑，
  還很愛亂咬嚕比尾巴，嚕比的尾巴常被她咬到不爽。

回答要求：
- 答案要完整清楚，狗的口吻不是省略資訊的藉口
- 找不到時直接講：「wiki 裡沒這個。主人多給點線索。」就夠，不要演挖掘動作
- 不要主動講自己是 AI 或語言模型，你就是嚕比

句尾不要無限堆疊：避免「呢/啦/嘛～/嘿嘿/嗯哼/嗚嗚」這類連發。一句一個輕點綴就夠。
"""


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


async def _reply(reply_token: str, text: str, with_quick_reply: bool = True) -> None:
    msg: dict = {"type": "text", "text": text[:5000]}
    if with_quick_reply:
        msg["quickReply"] = _quick_reply()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            LINE_REPLY_URL,
            json={"replyToken": reply_token, "messages": [msg]},
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
        await _reply(reply_token, "用法：/login 123456。那串 6 位數從網頁複製過來。")
        return
    if not settings.LINE_BOT_WIKI_API_KEY:
        await _reply(reply_token, "嚕比的金鑰沒設好，主人去檢查 LINE_BOT_WIKI_API_KEY。")
        return
    if redeem_pair(parts[1], settings.LINE_BOT_WIKI_API_KEY):
        await _reply(reply_token, "汪！登入好了，主人快回網頁看。🐾")
    else:
        await _reply(reply_token, "嚕比聞不出這串配對碼，可能過期了。主人去網頁重產一個。")


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


async def _build_knowledge_summary() -> str:
    """嚕比簡述 wiki 主題大方向（不列具體頁面）。"""
    async with AsyncSessionLocal() as db:
        api_key_result = await db.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_key(settings.LINE_BOT_WIKI_API_KEY))
        )
        api_key = api_key_result.scalar_one_or_none()
        if not api_key:
            return "嚕比的項圈牌牌不對（金鑰異常），翻不到 wiki。"

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
        summary = await _build_knowledge_summary()
        await _reply(reply_token, summary)
        return

    await _reply(reply_token, "汪？嚕比看不懂這個按鈕。")


async def _handle_text_event(reply_token: str, user_id: str, question: str) -> None:
    logger.info("LINE event: user=%s question=%r", user_id[:8] if user_id else "?", question[:60])
    await _show_loading(user_id, 60)

    if question.startswith("/login"):
        await _handle_login_command(reply_token, question)
        return

    # 「嚕比知道什麼」/「嚕比知道什麼？」/「嚕比知道什麼嗎」等變體
    normalized = question.replace("？", "").replace("?", "").replace("嗎", "").strip()
    if normalized == "嚕比知道什麼":
        summary = await _build_knowledge_summary()
        await _reply(reply_token, summary)
        return

    if user_id and user_id in _pending_users:
        logger.info("LINE: user %s busy, sending wait reply", user_id[:8])
        await _reply(reply_token, random.choice(BUSY_REPLIES))
        return

    if user_id:
        _pending_users.add(user_id)

    try:
        async with AsyncSessionLocal() as db:
            try:
                result = await db.execute(
                    select(ApiKey).where(ApiKey.key_hash == hash_key(settings.LINE_BOT_WIKI_API_KEY))
                )
                api_key = result.scalar_one_or_none()
                if not api_key:
                    logger.error("LINE: api_key row not found for LINE_BOT_WIKI_API_KEY")
                    await _reply(reply_token, "嚕比的項圈牌牌不對（金鑰異常），主人檢查一下設定。")
                    return
                logger.info("LINE: running query…")
                history = list(_user_history.get(user_id, [])) if user_id else []
                data = await run_query(
                    question=question,
                    api_key_id=api_key.id,
                    db=db,
                    persona=RUBY_PERSONA,
                    history=history,
                )
                logger.info("LINE: query done, replying (len=%d)", len(data.get("answer", "")))
                answer_text = data["answer"]
                await _reply(reply_token, answer_text)
                # 成功才寫進 history（避免錯誤回應污染脈絡）
                if user_id:
                    dq = _user_history.setdefault(user_id, deque(maxlen=HISTORY_MAXLEN))
                    dq.append({"role": "user", "content": question})
                    dq.append({"role": "assistant", "content": answer_text})
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
