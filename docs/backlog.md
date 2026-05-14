# 待開發清單

> 遵循 Karpathy LLM Wiki 設計理念。參考社群實作：[lucasastorian/llmwiki](https://github.com/lucasastorian/llmwiki)、[LLM Wiki v2](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)、[HN 討論](https://news.ycombinator.com/item?id=47899844)。

---

## P0 — Context Budget / 工程缺陷（2026-05-11 健檢）✅ 全數於 v0.4.6 修復

線上已發生「LLM 回空 / context window 爆掉」事故。根因是多個路徑沒做 context budget 管控；
`run_query` 已做 two-phase 減枝，其他三條路徑同樣高風險。

> v0.4.6 commit: `refactor(wiki): backend audit fixes — context budget, summary sync, lint batching`

### A. `run_query_stream` 沒做 context 減枝 ✅

**位置：** `backend/app/services/query_service.py` ~line 588-591

**現況：** streaming 版本將所有 `pages` 的 `p.content` 全塞進 `wiki_context`，無 budget cap。

**修法：** 套用跟 `run_query` 相同的 two-phase 減枝邏輯（先全頁配 summary，再用剩餘 budget 升級為 full content）；可抽出共用 helper `build_wiki_context(pages, max_chars=60000)`。

---

### B. `refine_wiki_plan` 沒做 context 減枝 ✅

**位置：** `backend/app/services/query_service.py` ~line 159-176

**現況：** `pages_ctx` 把 referenced_pages full content 全塞，且 `max_tokens=16384` 留給回應。先前 prod 出現的 `ContextWindowExceededError`（25827 messages + 16384 completion = 42211）就是這條。

**修法：** 同 A，套用 budget 減枝；或降低 max_tokens 上限。

---

### C. `back_link_pass` 沒做 context 減枝 ✅

**位置：** `backend/app/services/ingest.py` ~line 212-230

**現況：** new + old pages 全部 full content + `max_tokens=32768`。大 wiki ingest 必爆。

**修法：** 對 `new_pages` 和 `old_pages` 套用 budget 減枝；或限制 `old_pages` 取最近 N 頁。

---

### D. `apply_refine_plan` 新建頁面未初始化新欄位 ✅

**位置：** `backend/app/services/query_service.py` `apply_refine_plan`

**現況：** create 分支建立 `WikiPage` 時：
- 沒帶 `summary`（永遠空字串）
- 沒登記 `wiki_page_sources`（M2M 表沒記錄；雖然 chat 沒 source doc，但邏輯上應該明確標記）
- 沒設 `source_document_id`

**修法：**
- 讓 `RefinePlan.PageEdit` 增加 `summary` 欄位，prompt 要求 LLM 一併產生
- create 時把 `summary` 寫入 page 欄位
- chat 來源的頁面在 wiki_page_sources 用 NULL document_id 或新增 origin 標記

---

### E. `apply_lint_fixes` / `back_link_pass` 改寫 content 後 summary 不同步 ✅

**位置：** `backend/app/services/lint.py` `apply_lint_fixes`、`backend/app/services/ingest.py` `back_link_pass`

**現況：** 兩處都會改 `page.content`，但 `page.summary` 維持舊版本，造成索引/路由判斷依據過時。

**修法（擇一）：**
1. **同步重產：** 改 content 時順便讓 LLM 產生新 summary（多一次呼叫，貴）
2. **延遲標記：** 加欄位 `summary_stale: bool`，後台 backfill 補
3. **簡單比例規則：** content 字數變化 > 30% 才重產

---

### F. `route_query` 失敗 fallback 應該走 chat-only ✅

**位置：** `backend/app/services/query_service.py` ~line 297-299

**現況：** 路由 LLM 失敗 → `need_wiki=True`（保守 fallback），導致純閒聊也走 180 頁 wiki path。
線上「嚕比在嗎」就是被這條坑到。

**修法：** route 失敗時若 question 字數 < N（例如 20）→ 走 chat-only；長 question 才保守走 wiki。
或者加 regex 預過濾常見閒聊詞（「在嗎」「你好」「嚕比呢」）。

---

### G. `run_lint` 只看前 30 頁、JSON 解析無防呆 ✅

**位置：** `backend/app/services/lint.py` ~line 95、104-106

**現況：**
- `pages[:30]` 大 wiki 漏看 70%+
- `json.loads(json_str)` 沒 try/except，LLM 回非 JSON 直接 raise 500

**修法：**
- 改為分批掃描（每批 30 頁）並彙總
- JSON parse 失敗時走 fallback / 記 log

---

### H. `build_existing_wiki_context` 沒用新的 summary 欄位 ✅

**位置：** `backend/app/services/ingest.py` ~line 251-260

**現況：** 仍用 `content[:120]` 當摘要。新加的 `summary` 欄位閒置。

**修法：** 改為 `summary if summary else content[:120]`，跟其他路徑一致。

---

### I. ActivityLog `details` 沒有 schema 文件 ✅

**現況：** 各 action 的 details 結構各異：
- `chat`: `{}`
- `query`: `{pages_referenced, save_decision, edits_applied}`
- `ingest`: `{chunked, summary, filename, document_id, pages_created, back_link_edits}`
- `lint`: `{total_pages, issues_found}`
- `lint_apply`: `{requested, applied_pages, skipped}`

**修法：** `docs/` 增加 `activity-log-schema.md`，或改用 Pydantic discriminated union 落 schema。

---

### J. WikiPage 刪除沒記 ActivityLog ✅

**現況：** 文件刪 / wiki page 手動刪都沒留審計痕跡。

**修法：** `documents.py` 刪除路徑、`wiki.py` 刪頁路徑各加一筆 ActivityLog。

---

## P1 — 既有 backlog（部分已完成）

### 1. 跨文件主動回寫（Active Back-linking） ✅ 已實作

`backend/app/services/ingest.py` `back_link_pass` 已在 ingest 完成後執行。
但 context budget 缺管控（見 P0-C）。

---

### 2. LINE 對話 → Wiki（Chat-to-Wiki） ✅ 已實作

`run_query(save_to_wiki=True)` + `judge_save_decision` + `refine_wiki_plan` + `apply_refine_plan`。
但 create 路徑未初始化 summary / source 關聯（見 P0-D）。

---

### 3. LINE 路由優化（route_query 過濾冗贅） ✅ 已實作

`route_query` 在 `run_query` 開頭執行。`need_wiki=False` 直接走 `chat_only_reply`。
但失敗 fallback 對短訊息不友善（見 P0-F）。

---

## P2 — 品質提升

### 4. Lint 自動排程

**現況：** Lint 手動觸發。

**目標：** 定期自動執行，偵測並標記問題。

**兩層設計：**
1. **Programmatic 層**（秒級）：死連結、孤立頁、格式違規 — 純 DB/regex 掃描
2. **LLM 層**（分鐘級）：語意矛盾、過期聲明、缺少交叉引用

**觸發方式：** 後端 background task（每日）或現有 admin 手動觸發 + cron。

---

## P3 — 進階

### 5. Lint + Web Search（補缺口驗證）

Lint 掃到「過期聲明」時，可選擇性 web search 驗證。Karpathy 原文 optional 功能。

---

### 6. Typed Relationships（語意關係連結）

純 `[[wikilink]]` → 加語意關係（`depends-on`、`supersedes` 等）。500+ 頁規模才需要。

---

## 目前剩餘工項

| 順序 | 項目 | 狀態 |
|------|------|------|
| 1 | P2 #4 Lint 自動排程 | TODO（需 background scheduler） |
| 2 | P3 #5 Lint + Web Search | TODO（依賴 #4） |
| 3 | P3 #6 Typed Relationships | 規模未到（500+ 頁才需要） |

P0 A-J + P1 #1-3 已在 v0.4.6 / v0.4.7 完成。

---

## 參考資料

- [Karpathy 原始 gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [LLM Wiki v2 with typed relationships](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)
- [18 architectural extensions](https://gist.github.com/redmizt/968165ae7f1a408b0e60af02d68b90b6)
- [HN 討論](https://news.ycombinator.com/item?id=47899844)
- [Beyond RAG 分析](https://levelup.gitconnected.com/beyond-rag-how-andrej-karpathys-llm-wiki-pattern-builds-knowledge-that-actually-compounds-31a08528665e)
