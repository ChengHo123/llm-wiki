# LLM Wiki Platform — 開發技術文件

> 基於 Andrej Karpathy LLM Wiki 模式的個人知識庫平台。
> 核心理念：LLM 不只回答問題，而是持續維護一份結構化的 Markdown 知識庫，讓知識隨時間複利累積。

---

## 目錄

1. [系統架構概覽](#1-系統架構概覽)
2. [資料庫 Schema](#2-資料庫-schema)
3. [三大核心服務](#3-三大核心服務)
4. [API 端點規格](#4-api-端點規格)
5. [LLM 呼叫設計](#5-llm-呼叫設計)
6. [前端功能與 API 對應](#6-前端功能與-api-對應)
7. [例外情況與邊界處理](#7-例外情況與邊界處理)
8. [環境設定](#8-環境設定)
9. [未來擴充方向](#9-未來擴充方向)

---

## 1. 系統架構概覽

```
┌─────────────────────────────────────────────────────────────┐
│  使用者                                                       │
│  ├── 上傳文件（PDF / 圖片 / 文字）                             │
│  ├── 瀏覽 Wiki 頁面 & 知識圖譜                                 │
│  └── 查詢 Wiki                                               │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP + X-API-Key
┌──────────────────────▼──────────────────────────────────────┐
│  Frontend  React + Vite + Tailwind  :3000                   │
│  ├── Home    文件上傳 + API Key 管理                           │
│  ├── Wiki    頁面列表 + Markdown 瀏覽                          │
│  ├── Graph   知識圖譜視覺化（react-force-graph-2d）             │
│  └── Query   對話式查詢介面                                    │
└──────────────────────┬──────────────────────────────────────┘
                       │ nginx proxy → /api/*
┌──────────────────────▼──────────────────────────────────────┐
│  Backend  FastAPI + SQLAlchemy  :8000                       │
│  ├── /api/v1/keys          API Key 管理                      │
│  ├── /api/v1/documents     文件上傳與狀態查詢                   │
│  ├── /api/v1/wiki/*        Wiki 頁面 CRUD + 圖譜 + Lint       │
│  └── /api/v1/query         知識庫查詢                          │
└──────────┬───────────────────────────┬──────────────────────┘
           │                           │
┌──────────▼──────────┐   ┌────────────▼───────────────────────┐
│  PostgreSQL  :5432  │   │  LiteLLM / Groq / OpenAI           │
│  ├── api_keys       │   │  OpenAI-compatible API spec        │
│  ├── documents      │   │  設定 LLM_BASE_URL + LLM_API_KEY   │
│  ├── wiki_pages     │   └────────────────────────────────────┘
│  ├── wiki_links     │
│  └── activity_log   │
└─────────────────────┘
```

### 多租戶隔離

每個 `API Key` 對應一個獨立的 wiki 空間。所有 wiki_pages、documents、activity_log 都綁定 `api_key_id`，查詢時強制加上此條件，不同使用者的資料完全隔離。

---

## 2. 資料庫 Schema

### `api_keys` — 使用者憑證

| 欄位 | 類型 | 說明 |
|------|------|------|
| id | UUID PK | 唯一識別符 |
| key_hash | String(64) UNIQUE | API Key 的 SHA256 雜湊，**不存明文** |
| name | String(100) | 使用者自訂名稱 |
| created_at | DateTime | 建立時間 |

**關係：** 1:N → documents、wiki_pages、activity_log（全部 CASCADE delete）

---

### `documents` — 上傳文件

| 欄位 | 類型 | 說明 |
|------|------|------|
| id | UUID PK | — |
| api_key_id | UUID FK | 所屬使用者 |
| filename | String(255) | 原始檔名 |
| content_type | String(100) | MIME type |
| file_path | Text | 伺服器儲存路徑 `/app/uploads/{api_key_id}/{uuid}_{filename}` |
| status | String(20) | `pending` → `processing` → `done` / `error` |
| error_message | Text nullable | Ingest 失敗時的錯誤訊息 |
| created_at | DateTime | — |

---

### `wiki_pages` — Wiki 頁面

| 欄位 | 類型 | 說明 |
|------|------|------|
| id | UUID PK | — |
| api_key_id | UUID FK | 所屬使用者 |
| source_document_id | UUID FK nullable | 最初產生此頁面的文件 ID（`SET NULL` on delete） |
| title | String(500) | 頁面標題 |
| slug | String(500) | URL-friendly kebab-case 識別碼 |
| content | Text | Markdown 格式內容 |
| page_type | String(50) | `index` / `summary` / `entity` / `concept` |
| created_at | DateTime | — |
| updated_at | DateTime | 每次 upsert 更新 |

**Index：** `UNIQUE(api_key_id, slug)` — 同一使用者內 slug 不重複，是 upsert 的依據

**source_document_id 規則：**
- 頁面由 ingest 首次建立時記錄來源文件
- 後續其他文件 upsert 同一頁面時**不覆蓋**，保留最初來源
- 刪除來源文件時，此欄位 `SET NULL`（頁面保留）；若使用 `DELETE /documents/{id}?delete_pages=true` 則連帶主動刪除

**page_type 說明：**
- `index`：知識索引頁，彙整多個主題的入口
- `summary`：某份文件的摘要頁
- `entity`：人名、組織、產品等具體實體
- `concept`：技術概念、術語定義

---

### `wiki_links` — 頁面交叉連結

| 欄位 | 類型 | 說明 |
|------|------|------|
| id | UUID PK | — |
| source_page_id | UUID FK | 連結來源頁面（CASCADE delete） |
| target_page_id | UUID FK | 連結目標頁面（CASCADE delete） |
| link_text | String(255) nullable | 連結說明文字 |

**用途：** 此表的所有邊構成知識圖譜。`GET /wiki/graph` 直接返回這些節點與邊。

---

### `activity_log` — 操作日誌

| 欄位 | 類型 | 說明 |
|------|------|------|
| id | UUID PK | — |
| api_key_id | UUID FK | 操作的使用者 |
| action | String(50) | `ingest` / `query` / `lint` |
| details | JSONB | 操作細節（見下方） |
| created_at | DateTime | — |

**details 結構：**

```jsonc
// ingest
{ "document_id": "uuid", "filename": "doc.pdf", "pages_created": 12, "summary": "..." }

// query
{ "question": "...", "pages_referenced": 5, "saved_to_wiki": false }

// lint
{ "total_pages": 30, "issues_found": 3 }
```

---

## 3. 三大核心服務

### 3.1 Ingest — `app/services/ingest.py`

**觸發：** `POST /documents` 上傳成功後，以 `BackgroundTask` 非同步執行

#### 流程

```
上傳文件
    │
    ▼
document.status = "processing"
    │
    ▼
依檔案類型預處理
    ├── 圖片（.png/.jpg/.gif/.webp）→ base64 → OpenAI image_url content block
    ├── PDF（.pdf）               → pypdf 抽文字 → 純文字訊息
    └── 文字（.txt/.md/.csv 等）  → 讀取文字 → 純文字訊息
    │
    ▼
呼叫 LLM（INGEST_SYSTEM_PROMPT）max_tokens=8192
    │
    ▼
解析 JSON 回應（含 ```json...``` 包裝處理）
    │
    ▼
逐頁 UPSERT wiki_pages（以 api_key_id + slug 為 key）
    │
    ▼
重建 wiki_links（先清舊連結，再依 links_to 建新連結）
    │
    ▼
document.status = "done" + 寫入 activity_log
    │
    └── 若任何步驟拋例外 → document.status = "error" + error_message
```

#### Slug 生成規則

```python
def slugify(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or f"page-{uuid.uuid4().hex[:8]}"  # 空結果時隨機生成
```

---

### 3.2 Query — `app/services/query_service.py`

**觸發：** `POST /query`

#### 流程

```
接收問題
    │
    ▼
搜尋相關 Wiki 頁面（最多 8 頁）
  → 對問題分詞（最多 5 個關鍵字）
  → PostgreSQL ILIKE 搜尋 title + content
  → 若無結果：fallback 取最新 10 頁
    │
    ▼
組建 wiki context（XML 格式，供 LLM 讀取）
    │
    ▼
呼叫 LLM（QUERY_SYSTEM_PROMPT）max_tokens=2048
    │
    ▼
若 save_to_wiki = true：
  → 建立 page_type=concept 的新頁面（slug: query-{問題前50字}）
  → UPSERT（相同問題再次存檔時更新）
  → 建立 WikiLink 連接到所有被參考的頁面
    │
    ▼
寫入 activity_log
    │
    ▼
回傳 { answer, referenced_pages, saved_page }
```

#### 重要設計決策

- **兩階段 LLM 搜尋**：Phase 1 送所有頁面標題讓 LLM 選出相關頁面（語意選擇，支援中文），Phase 2 再用完整內容回答。避免用字串比對（ILIKE/n-gram）來判斷語意相關性。
- **存回 wiki 的頁面會連結到被參考的頁面**：讓 Q&A 結果成為圖譜的一部分，而非孤立節點

#### Phase 1 — 頁面選擇 Prompt

```
系統：你是一個知識庫搜尋助手。
      給定問題和 wiki 頁面列表，挑出最相關的頁面（最多 8 個）。
      只回傳 JSON：{"relevant_slugs": ["slug1", "slug2"]}

使用者：問題：{question}

Wiki 頁面列表：
- slug: "aquarium", title: "水族箱", type: concept
- slug: "fish-care", title: "魚類照護", type: concept
...
```

- **輸出：** `{"relevant_slugs": [...]}` — max_tokens: 256（便宜）
- **Fallback：** 若 LLM 回傳空列表，改取最新 8 頁

---

### 3.3 Lint — `app/services/lint.py`

**觸發：** `POST /wiki/lint`（手動觸發，非排程）

#### 流程

```
載入所有 wiki_pages
    │
    ├── 若 0 頁 → 提前返回空報告
    │
    ▼
計算孤立頁面
  → 找出所有有出站連結的頁面（linked_sources）
  → 找出所有有入站連結的頁面（linked_targets）
  → orphan_ids = page_ids - linked_sources - linked_targets
    │
    ▼
組建 pages_summary（每頁截 800 字，最多送 30 頁）
  → 每頁標記 orphan 狀態
    │
    ▼
呼叫 LLM（LINT_SYSTEM_PROMPT）max_tokens=4096
    │
    ▼
解析 JSON 回應
    │
    ▼
用實際計算值覆蓋 LLM 的 stats（LLM 統計可能不準）
    │
    ▼
寫入 activity_log + 回傳報告
```

#### 限制

- 目前每次最多分析 30 頁（避免 token 爆炸）
- 超過 30 頁的 wiki 只有前 30 頁會被 LLM 分析，但孤立頁統計仍包含全部頁面

---

### 3.4 Ingest 已知缺陷（參考 Karpathy 模式研究）

對照 [Karpathy LLM Wiki Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 分析，現行 ingest 有以下結構性問題：

| 缺陷 | 說明 | Karpathy 解法 |
|------|------|--------------|
| **跨文件 relation 不存在** | `slug_to_id` 只含本次 ingest 批次，第二份文件無法連結到第一份產生的頁面 | Ingest 前先讀 `index.md`（所有頁面目錄），LLM 知全局再決定連結 |
| **`[[標題]]` 與 `links_to` 未對齊** | content 內的 wiki 語法與 DB links 各自獨立，可能不一致 | 兩者統一由 LLM 維護，同一來源 |
| **slug 斷鏈** | `links_to` 依賴 LLM 給出精確 slug，無 fuzzy matching | index 提供 slug 清單，LLM 對照選取 |
| **無全局知識感知** | LLM 看不到已存在的概念頁，可能重複建立相似頁面 | LLM 讀 index 後主動 upsert 相關頁，而非盲目新建 |

**修正方向：** ingest 前先載入現有 wiki 所有頁面的 `(title, slug, 摘要)` 作為 context 注入 prompt，讓 LLM 知曉全局，自然能建跨文件連結。

---

## 4. API 端點規格

所有端點前綴：`/api/v1`
認證方式：`X-API-Key: {key}` Header

### POST `/keys` — 建立 API Key

**Request Body:**
```json
{ "name": "string" }
```

**Response 200:**
```json
{
  "key": "wk_xxxxxxxxxxxxxxxx",
  "name": "string",
  "message": "請保存此 API Key，之後將無法再次查看"
}
```

> Key 只在建立時回傳一次，後端只存 SHA256 雜湊，無法還原明文。

---

### POST `/documents` — 上傳文件

**Request:** `multipart/form-data`，欄位名 `file`

**支援 MIME 類型：**
```
application/pdf
image/png, image/jpeg, image/gif, image/webp
text/plain, text/markdown, text/csv
application/json
```

**Response 200:** Document 物件（status 為 `pending`）

**錯誤：**
- `401` — API Key 無效
- `413` — 超過 `MAX_UPLOAD_SIZE_MB`（預設 50MB）
- `415` — 不支援的 MIME 類型

**Note:** 上傳後 ingest 為非同步，需輪詢 `GET /documents` 確認 status 變化。

---

### GET `/documents` — 列出文件

**Response 200:** `Document[]`，按 `created_at` 倒序

---

### DELETE `/documents/{document_id}` — 刪除文件

**Query Params:**
- `delete_pages: bool`（預設 `true`）— 是否一併刪除由此文件產生的 wiki 頁面

**處理步驟：**
1. 確認文件屬於當前 API Key（否則 404）
2. 若 `delete_pages=true`：查詢 `source_document_id = document_id` 的所有 wiki_pages 並刪除
3. 刪除伺服器上的實體檔案（`missing_ok=True`，不存在不報錯）
4. 刪除 documents 記錄

**Response 200:**
```json
{ "deleted_document_id": "uuid", "pages_deleted": 5 }
```

**注意：** wiki_links 因 CASCADE 自動清除，不會留下孤立連結。

---

### GET `/wiki/pages` — 列出 Wiki 頁面

**Response 200:** `WikiPageSummary[]`（不含 content），按 `updated_at` 倒序

---

### DELETE `/wiki/pages/{page_id}` — 刪除 Wiki 頁面

**處理步驟：**
1. 確認頁面屬於當前 API Key（否則 404）
2. 刪除 WikiPage 記錄（wiki_links 因 CASCADE 自動清除）

**Response 200:**
```json
{ "deleted_page_id": "uuid" }
```

---

### GET `/wiki/pages/{page_id}` — 取得頁面詳情

**Response 200:** `WikiPageDetail`（含 content Markdown）

**錯誤：**
- `404` — 頁面不存在，或不屬於該 API Key

---

### GET `/wiki/graph` — 知識圖譜資料

**Response 200:**
```json
{
  "nodes": [
    { "id": "uuid", "title": "string", "slug": "string", "page_type": "string" }
  ],
  "edges": [
    { "source": "uuid", "target": "uuid", "link_text": "string | null" }
  ]
}
```

---

### POST `/wiki/lint` — Wiki 健檢

**Response 200:**
```json
{
  "issues": [
    {
      "type": "contradiction | stale | orphan | missing_link | incomplete",
      "severity": "high | medium | low",
      "page_slug": "string",
      "description": "string",
      "suggestion": "string"
    }
  ],
  "stats": {
    "total_pages": 30,
    "orphan_pages": 3,
    "issues_found": 5
  },
  "summary": "整體健康狀況摘要"
}
```

---

### POST `/query` — 查詢 Wiki

**Request Body:**
```json
{
  "question": "string",
  "save_to_wiki": false
}
```

**Response 200:**
```json
{
  "answer": "Markdown 格式的回答",
  "referenced_pages": [
    { "id": "uuid", "title": "string", "slug": "string" }
  ],
  "saved_page": { "id": "uuid", "title": "string", "slug": "string" } | null
}
```

---

## 5. LLM 呼叫設計

### 5.1 Client 設定

```python
# app/services/llm.py
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,   # 支援任何 OpenAI spec endpoint
)
```

**Multimodal 相容性：** 若模型不支援 vision（list content），`_flatten_content()` 會自動把圖片 block 壓平為文字 `[圖片內容，模型不支援視覺輸入]`，不會拋出 400 錯誤。支援 vision 的模型（如生產環境的 GPT-4o）則直接傳 list content。

---

### 5.2 Ingest Prompt

**輸出格式：JSON（LLM 可能包在 ` ```json ``` ` 內）**

```
系統：你是一個知識整理助手，負責將文件內容整合進個人 wiki 知識庫。
      分析文件，產生 10~15 個結構化的 wiki 頁面。

回傳 JSON 結構：
{
  "pages": [
    {
      "title": "頁面標題",
      "slug": "kebab-case",
      "page_type": "summary|entity|concept|index",
      "content": "Markdown 內容（用 [[標題]] 語法標記交叉連結）",
      "links_to": ["target-slug-1", "target-slug-2"]
    }
  ],
  "summary": "文件摘要"
}
```

**max_tokens: 8192**（頁面多，需要較大 token 空間）

---

### 5.3 Query Prompt

**輸出格式：純 Markdown 文字**

```
系統：你是一個個人知識庫助理。
      根據 <wiki>...</wiki> 中的頁面內容回答問題。
      若 wiki 無相關資訊請明確說明。
      引用的頁面用 [[頁面標題]] 標記。

<wiki>
<wiki_page title="..." slug="...">
{頁面 content}
</wiki_page>
...
</wiki>

使用者：{問題}
```

**max_tokens: 2048**

---

### 5.4 Lint Prompt

**輸出格式：JSON（LLM 可能包在 ` ```json ``` ` 內）**

```
系統：你是一個知識庫品質審查員。
      分析 wiki 頁面，找出矛盾、過時、孤立、缺連結、內容不完整等問題。
      以 JSON 格式回傳 { issues, stats, summary }。

使用者：請分析以下 wiki 頁面：
<page slug="..." type="..." orphan="true|false">
{頁面前 800 字}
</page>
...（最多 30 頁）
```

**max_tokens: 4096**

---

## 6. 前端功能與 API 對應

### 路由結構

```
/        Home   — API Key 設定 + 文件上傳
/wiki    Wiki   — 頁面列表 + 詳情瀏覽 + Lint
/graph   Graph  — 知識圖譜視覺化
/query   Query  — 對話式查詢
```

### API Key 儲存

Key 存在 `localStorage['llm_wiki_api_key']`，由 `axios interceptors` 自動帶入每個請求的 `X-API-Key` header。

### 文件狀態輪詢

Home 頁面每 3 秒自動呼叫 `GET /documents`，直到所有文件不再是 `pending` 或 `processing` 狀態。

### 圖譜互動

| 動作 | 效果 |
|------|------|
| 點擊節點 | 右側面板展開頁面內容 |
| 懸停節點 | 節點放大 + 白色外框 |
| 圖表載入完成 | 自動 `zoomToFit` |
| 右上角按鈕 | 放大 1.5x / 縮小 0.7x / 自動 fit |

**節點顏色：**
- 紫 `#8b5cf6` — index
- 藍 `#3b82f6` — summary
- 綠 `#10b981` — entity
- 橘 `#f59e0b` — concept

---

## 7. 例外情況與邊界處理

### 檔案上傳層

| 情況 | 處理 |
|------|------|
| 不支援的 MIME type | 415，拒絕上傳 |
| 超過大小限制 | 413，拒絕上傳（nginx 層 `client_max_body_size 100M`） |
| API Key 無效 | 401 |
| 磁碟空間不足 | 500，檔案寫入失敗 |

### Ingest 層

| 情況 | 處理 |
|------|------|
| PDF 解析失敗 | 記錄 `"(PDF 解析失敗)"`，以空文字繼續 |
| LLM 回應非合法 JSON | 嘗試從 ` ```json ``` ` 提取；失敗則 `document.status = "error"` |
| LLM 回應的 slug 為空 | `slugify()` 生成；若仍空則用 `page-{random_hex}` |
| 連結指向不存在的 slug | 靜默忽略，不建立懸空連結 |
| 同一文件重複上傳 | 以 `(api_key_id, slug)` UPSERT，更新現有頁面，不重複建立 |
| 模型不支援 vision | `_flatten_content()` 自動壓平 list content，改以文字傳送 |

### Query 層

| 情況 | 處理 |
|------|------|
| Phase 1 LLM 回傳空列表 | Fallback 取最新 8 頁作為 context |
| Phase 1 LLM 回傳非法 JSON | 捕獲例外，fallback 取最新 8 頁 |
| Wiki 完全為空 | Phase 1 提前返回空列表，Phase 2 收到空 context，LLM 說明無相關資訊 |
| `save_to_wiki=true` 且問題相同 | UPSERT，更新既有 query-* 頁面，並重建連結 |
| 問題過長（> 100 字） | slug 只取前 50 字，title 只取前 100 字 |

### 刪除層

| 情況 | 處理 |
|------|------|
| 刪除不屬於自己的文件/頁面 | 404（查詢時已加 api_key_id 條件） |
| 實體檔案已不存在 | `missing_ok=True`，不報錯繼續刪 DB 記錄 |
| 頁面被多份文件 upsert 過 | source_document_id 記錄第一份文件，只刪第一份文件時才連帶刪頁面 |
| 刪除 wiki 頁面後的孤立連結 | CASCADE delete 自動清除 wiki_links |

### Lint 層

| 情況 | 處理 |
|------|------|
| Wiki 為空 | 提前返回空報告，不呼叫 LLM |
| 頁面數超過 30 | 只送前 30 頁給 LLM，`stats` 仍基於全部頁面計算 |
| LLM 回應非合法 JSON | 500 錯誤（呼叫方需處理） |
| LLM 的 stats 不準 | 用 Python 實際計算值覆蓋 |

---

## 8. 環境設定

### 環境變數設定

LLM 相關變數直接寫在 `docker-compose.yml` 的 `backend.environment` 區塊，不使用 `.env` 檔（避免被打包進 image）。

```yaml
# docker-compose.yml
services:
  backend:
    environment:
      LLM_BASE_URL: http://host.docker.internal:11434/v1   # Ollama 或任何 OpenAI-compatible endpoint
      LLM_API_KEY: ollama                                   # Ollama 不驗證，填任意值
      LLM_MODEL: gemma4:31b
      DATABASE_URL: postgresql+asyncpg://wiki:wiki@postgres:5432/wiki
      UPLOAD_DIR: /app/uploads
```

| 變數 | 說明 |
|------|------|
| `LLM_BASE_URL` | OpenAI-compatible endpoint，需含 `/v1` |
| `LLM_API_KEY` | 對應 API Key；Ollama 填任意值 |
| `LLM_MODEL` | 模型名稱 |
| `DATABASE_URL` | PostgreSQL 連線字串 |
| `UPLOAD_DIR` | 上傳目錄（容器內路徑） |

### 連接區網 Ollama（Windows Docker Desktop）

容器無法直連 LAN IP（Docker Desktop 走 WSL2 虛擬網路），需在 Windows 設定 port forward：

```powershell
# 管理員 PowerShell — 將容器流量轉發至區網 Ollama 主機
netsh interface portproxy add v4tov4 listenport=11434 listenaddress=0.0.0.0 connectport=11434 connectaddress=192.168.0.X
netsh interface portproxy add v4tov4 listenport=11434 listenaddress=192.168.65.254 connectport=11434 connectaddress=192.168.0.X

# 開防火牆
New-NetFirewallRule -DisplayName "Ollama Docker Forward" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow
```

`docker-compose.yml` backend 需有：
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

### 啟動

```bash
docker compose up --build
```

- 前端：`http://localhost:3000`
- API 文件：`http://localhost:8000/docs`
- pgAdmin：`http://localhost:5050`（帳號 `admin@admin.com` / `admin`，DB host 填 `postgres`）

---

## 9. 未來擴充方向

### 功能面

- **跨文件 relation（高優先）**：ingest 前注入現有 wiki index（全部頁面的 title + slug + 摘要）至 prompt，讓 LLM 建立跨文件連結，對齊 Karpathy 模式
- **自動來源蒐集**：RSS feed 訂閱、arXiv 論文爬取、網頁 URL 匯入
- **排程 Lint**：每天/每週自動執行 wiki 健檢
- **Wiki 頁面手動編輯**：PATCH `/wiki/pages/{id}` 讓使用者修正 LLM 的錯誤
- **多文件關聯**：記錄每個 wiki 頁面是由哪些文件 ingest 產生的
- **版本歷史**：wiki 頁面的修改紀錄（類似 git diff）

### 效能面

- **全文搜尋優化**：PostgreSQL `tsvector` + GIN index 取代 ILIKE
- **Ingest 任務佇列**：用 Celery/ARQ 取代 BackgroundTask，支援重試與監控
- **Lint 分頁處理**：超過 30 頁時分批送給 LLM 再合併報告

### 微服務面

- **Webhook 回調**：ingest 完成時主動通知外部服務
- **OpenAPI SDK 生成**：基於 FastAPI 自動產生客戶端 SDK
- **Rate limiting**：每個 API Key 的請求頻率控制
