# LINE Bot 每用戶獨立 Wiki — 完整技術文件

> 本文件為合併版（取代舊的 `linebot-get-link-plan.md`）。
> 涵蓋：每用戶獨立 wiki、Web Session 認證、Rich Menu 一鍵登入連結。

---

## 1. 設計概念

### 1.1 LINE 用戶的 wiki 隔離

LINE 用戶加入好友（或第一次發訊息）時，自動建立一組 ApiKey 與獨立 wiki。
之後該用戶的所有查詢都使用這組自動建立的 wiki，不需配對任何碼。

```
用戶加好友 / 首次發訊息
    → 查 line_user_bindings 有無記錄
    → 無 → 自動建立 ApiKey + 寫入 binding
    → 有 → 直接用現有 api_key_id 查 wiki
```

### 1.2 網頁存取（一鍵登入連結）

LINE Official Account Manager 設定 Rich Menu 按鈕（或關鍵字「取得連結」），
觸發後 bot 推送一個帶 session token 的前端網址，
用戶點連結就直接登入自己的 wiki。網頁端沒有任何手動登入流程。

```
用戶按 Rich Menu「取得 wiki 連結」按鈕
    → bot 收到 postback (action=get_link)
    → _get_or_create_api_key 取/建 api_key
    → 建 WebSession（24 小時 TTL）
    → reply 連結 "FRONTEND_URL/?token=ws_..." 給用戶
    → 用戶點連結
    → 前端 main.tsx 啟動時讀 ?token=ws_...
    → 存入 localStorage、清除 URL param
    → axios interceptor 用 X-Session-Token 帶到後端
```

### 1.3 為什麼不暴露原始 ApiKey

auto-created ApiKey 的 raw key 只在建立瞬間存在，DB 只保留 hash。
用戶不該也不需要拿到 raw key。網頁改用獨立的 **WebSession token** 機制：

- Raw API Key 永遠不離開 server
- Session 24 小時自動到期（連結若被截圖外洩，曝露窗口短）
- 過期重按按鈕拿新連結即可

---

## 2. Schema

### 2.1 `line_user_bindings`

| 欄位 | 型別 | 說明 |
|------|------|------|
| `line_user_id` | String(64) PK | LINE platform userId |
| `api_key_id` | UUID FK → api_keys.id | 自動建立的 wiki key |
| `created_at` | DateTime | 首次加入時間 |

### 2.2 `web_sessions`

| 欄位 | 型別 | 說明 |
|------|------|------|
| `session_token` | String(128) PK | 隨機 token（`ws_` 前綴 + 48 bytes urlsafe） |
| `api_key_id` | UUID FK → api_keys.id | 對應的 wiki |
| `created_at` | DateTime | 建立時間 |
| `expires_at` | DateTime | 到期時間（預設 24 小時） |

Migration：`backend/alembic/versions/0003_line_bindings_and_sessions.py`

---

## 3. 後端實作

### 3.1 新增 model

- `backend/app/models/line_user_binding.py`
- `backend/app/models/web_session.py`（含 `SESSION_TTL_HOURS = 24`）

### 3.2 `core/security.py`

新增 `generate_session_token()`，回傳 `ws_<48 bytes urlsafe>`。

`get_current_key` dependency 同時接受兩種 header（皆 `auto_error=False`）：
- `X-API-Key` → 查 `api_keys.key_hash`
- `X-Session-Token` → 查 `web_sessions`，需 `expires_at > now()`

兩者皆無或無效則 401。

### 3.3 `core/config.py`

新增：
```python
FRONTEND_URL: str = "http://localhost:3000"
```

移除（已不使用）：
```python
LINE_BOT_WIKI_API_KEY: str  # 舊的單一共用 key
```

### 3.4 ~~`services/login_pairing.py`~~（已刪除）

原本給 `/weblink` 6 位碼配對用的 in-memory store。
網頁端改純走 Rich Menu 一鍵連結後此檔已整檔刪除。

### 3.5 `api/v1/linebot.py`

**新增 helper `_get_or_create_api_key(line_user_id, db) → ApiKey`：**
- 查 `line_user_bindings` 找現有 binding
- 找到 → 回傳對應 `ApiKey`
- 找不到 → 建新 `ApiKey`（name=`LINE:{line_user_id[:8]}`）+ binding，回傳

**新增 `follow` event 處理：**
- 自動 `_get_or_create_api_key`
- push 歡迎訊息（提示按 Rich Menu「取得 wiki 連結」）

**新增 postback `action=get_link` 處理：**
- `_get_or_create_api_key`
- 建 `WebSession`
- reply `{FRONTEND_URL}/?token={session_token}`，提示 24 小時內有效

**`_handle_text_event`：** 用 `_get_or_create_api_key` 取得當前用戶 api_key，再 `run_query`
**`_build_knowledge_summary`：** 改吃 `api_key: ApiKey` 參數

### 3.6 `api/v1/auth.py`

僅保留 `/keys`、`/keys/me`。原本的 `line-pair`、`line-web/start,poll` 全數移除，
網頁端不再透過 6 位碼配對流程登入。

`/keys/me`：用 `get_current_key`，X-API-Key 或 X-Session-Token 都行。

---

## 4. 前端實作

### 4.1 `src/main.tsx`

啟動時讀 URL `?token=ws_...`，存入 localStorage 後清除 URL param：

```typescript
function consumeUrlToken() {
  const params = new URLSearchParams(window.location.search)
  const token = params.get('token')
  if (!token || !token.startsWith('ws_')) return
  addStoredKey(params.get('name') || 'LINE', token)
  // 清除 URL 中的 token / name
  params.delete('token'); params.delete('name')
  const qs = params.toString()
  window.history.replaceState({}, '', window.location.pathname + (qs ? `?${qs}` : ''))
}
consumeUrlToken()
```

### 4.2 `src/api/client.ts`

axios interceptor 依儲存值前綴選 header：
- `wk_` → `X-API-Key`
- `ws_` → `X-Session-Token`

streaming endpoint 同邏輯。

`startLineWeb` / `pollLineWeb` 取代 `startLinePair` / `pollLinePair`。

### 4.3 `src/pages/Home.tsx`

桌面端只剩：建 Key、輸入 raw key 登入、切換已存 key。
LINE 配對 UI（產生配對碼）已移除——LINE 用戶請走 Rich Menu 一鍵連結進入。

---

## 5. LINE OA Manager 設定（不動 code）

LINE Official Account Manager → Rich Menu → 新增按鈕：
- **動作類型：** Postback
- **Postback data：** `action=get_link`
- **顯示文字（optional）：** 取得 wiki 連結

部署時不需要重 deploy；OA Manager 改完即時生效。

---

## 6. 用戶流程總覽

```
LINE 加好友 → 自動建 wiki → 收到歡迎訊息（提示按 Rich Menu）

主流程（一鍵連結）：
  按「取得 wiki 連結」按鈕 / 傳「取得連結」訊息 → 收到網址
  點網址 → 前端自動登入 → 上傳文件、查 wiki

LINE 發問：
  發訊息 → bot 查自己的 wiki 回答（自動帶 line_user_id 的 api_key）

到期：
  WebSession 24 小時後失效 → 401 → 重按 Rich Menu 拿新連結
```

---

## 7. 環境變數

| Var | 說明 | 預設 |
|-----|------|------|
| `LINE_CHANNEL_SECRET` | LINE Channel Secret | — |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Channel Access Token | — |
| `FRONTEND_URL` | bot push 連結用的網址前綴 | `http://localhost:3000` |
| ~~`LINE_BOT_WIKI_API_KEY`~~ | **已移除** | — |

---

## 8. 風險點

| 風險 | 說明 | 緩解 |
|------|------|------|
| 連結外洩 | URL 含 token，截圖轉傳可被他人使用 | 24 小時 TTL；用戶若察覺可重產新連結（舊的繼續有效但用處有限） |
| 大量帳號 | 每個加好友的人都建一組 ApiKey，無上限 | 視部署規模監控；目前 `MAX_WIKI_PAGES` 限制單 wiki 容量 |
| 重複按按鈕 | 每次按都建新 WebSession，舊 session 仍有效 | 可接受；DB 增量小。日後可加 reuse 邏輯 |

---

## 9. 實作順序（已完成）

1. ✅ 新 Model `LineUserBinding` + `WebSession`
2. ✅ Alembic migration `0003_line_bindings_and_sessions`
3. ✅ `linebot.py` — `_get_or_create_api_key` helper、follow event、per-user query
4. ✅ `auth.py` — 只剩 `/keys`、`/keys/me`
5. ✅ `security.py` — X-Session-Token 支援
6. ✅ `config.py` — 加 `FRONTEND_URL`、移除 `LINE_BOT_WIKI_API_KEY`
7. ✅ docker-compose / .env.example 同步
8. ✅ `linebot.py` — postback `action=get_link` handler
9. ✅ `web_session.py` — TTL 改 24 小時
10. ✅ frontend `main.tsx` — URL token 自動登入
11. ✅ frontend `client.ts` — `ws_` 前綴判斷 → X-Session-Token
12. ⏳ LINE OA Manager 設定 Rich Menu 按鈕（手動，不在 code）
