# 多使用者服務限流設計（簡化版）

## 設計原則

**核心理念：盡量讓 LiteLLM 處理 RPM 限制，後端只負責處理 429 與防腳本。**

未來換付費模型時，只要改 LiteLLM UI 裡的數字就好，後端 code 不用動。

---

## 現況分析

- **Nvidia 免費模型上限：40 RPM**
- 1 則 LINE 訊息最壞情況 = **5 次 LLM 呼叫**（route + select + answer + judge + refine）
- 現有保護：只有 `_pending_users`（同一使用者同時只能有 1 個進行中請求）

**風險：**
1. 多人同時用 → LiteLLM 直接 429 → 使用者收到「嚕比被書絆倒了」這種模糊錯誤
2. LINE 不限制訊息頻率 → 惡意腳本可塞爆後端

---

## 三層防護

```
LINE Webhook
     │
     ▼
[Layer A] Per-user 防腳本（in linebot.py）
  - 訊息冷卻 5 秒
  - 每分鐘上限 5 則
     │
     ▼
[Layer B] LiteLLM RPM 限制（UI 設定）
  - rpm_limit = 38（留 2 RPM 緩衝）
     │
     ▼
[Layer C] 429 友善回覆（in linebot.py）
  - 抓 openai.RateLimitError → 回「嚕比現在很忙」
     │
     ▼
Nvidia / 付費模型
```

---

## Layer B：LiteLLM RPM 限制（最簡單）

**設定方式：LiteLLM UI**

1. 登入 LiteLLM UI（雲端：`https://<DOMAIN>/litellm/ui`）
2. Models → 找到目前用的 model（`qwen/qwen3.5-122b-a10b`、`meta/llama-3.2-90b-vision-instruct`）
3. Edit → 設定 **RPM Limit = 38**（給 Nvidia 免費版用，留 2 RPM 緩衝）
4. 儲存

未來換付費模型時，只要回到這個畫面改數字即可，後端 code 完全不動。

**為什麼是 38 而不是 40：**
- 留 2 RPM 緩衝給時鐘漂移、retry、ingest queue 偶爾的並發

---

## Layer C：429 友善回覆

LiteLLM 超過 RPM 會回 HTTP 429，OpenAI Python client 會 raise `openai.RateLimitError`。

修改 `backend/app/api/v1/linebot.py` 的 `_handle_text_event`，把這個錯誤特別處理：

```python
import openai

# 在 _handle_text_event 的 try/except 裡：
try:
    api_key = await _get_or_create_api_key(user_id, db)
    history = list(_user_history.get(user_id, [])) if user_id else []
    data = await run_query(
        question=question,
        api_key_id=api_key.id,
        db=db,
        persona=RUBY_PERSONA,
        history=history,
    )
    ...
except openai.RateLimitError:
    logger.warning("LINE query hit rate limit (429)")
    await _reply(
        reply_token,
        "汪！嚕比現在被太多主人圍住，喘不過氣。等個 30 秒再問嚕比一次。"
    )
except Exception:
    logger.exception("LINE query error")
    await _reply(
        reply_token,
        "汪！嚕比剛剛被書絆倒了，主人再丟一次問題過來。"
    )
```

> **注意**：`run_query` 內部會打多次 LLM，任何一次 429 都會 propagate 上來，不需在 `llm.py` 內額外處理。

---

## Layer A：Per-user 防腳本

**只限制單一使用者，不限制全體**——惡意腳本只能拖累自己，不影響其他正常使用者。

設定值放在 env，預設 5：

```bash
# .env
LINE_USER_COOLDOWN_SECONDS=5   # 同一 LINE 使用者兩則訊息間最短間隔
LINE_USER_RPM_LIMIT=5          # 同一 LINE 使用者每分鐘最多訊息數
```

`backend/app/core/config.py` 已加上對應欄位（預設值都是 5）。
`docker-compose.yml` / `docker-compose.prod.yml` 都已透傳這兩個 env 給 backend。

**實作：** `backend/app/api/v1/linebot.py` 內定義 `_check_user_rate_limit(user_id)`：
- 用兩個 in-memory dict 追蹤：
  - `_user_last_message_at`：上次訊息時間 → 冷卻判斷
  - `_user_minute_calls`：滑動視窗 deque → 每分鐘上限
- 通過 → 記錄並回 None；被擋 → 回友善訊息字串
- 在 `_handle_text_event` 開頭呼叫，被擋直接 `_reply` 友善訊息後 return（不浪費 LLM 配額）

**設計考量：**
- 5 秒冷卻 + 每分鐘 5 則 → 正常人對話不會被擋（人類打字 + 思考 5 秒以上正常）
- 腳本若每秒打 1 次 → 第 2 則就被擋，後續每 5 秒被擋一次，自動降速
- in-memory dict 即可，重啟後資料消失沒關係（重啟後本來就無歷史紀錄要保留）
- 不影響其他使用者（key by `user_id`）

---

## 數字設定速查

| 參數 | 預設值 | 在哪裡設 | 換付費模型時 |
|---|---|---|---|
| 模型 RPM | 38 | LiteLLM UI | 改 UI 數字 |
| `LINE_USER_COOLDOWN_SECONDS` | 5 | `.env` | 視需求調整 |
| `LINE_USER_RPM_LIMIT` | 5 | `.env` | 視需求調整 |

---

## 實作順序（從快到慢）

1. **LiteLLM UI 設 RPM = 38**（5 分鐘）
2. **Layer C：linebot.py 加 429 catch**（10 分鐘）
3. **Layer A：per-user rate limit**（15 分鐘）

三步加起來不到半小時，全部完成後就有完整防護。
