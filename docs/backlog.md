# 待開發清單

> 遵循 Karpathy LLM Wiki 設計理念。參考社群實作：[lucasastorian/llmwiki](https://github.com/lucasastorian/llmwiki)、[LLM Wiki v2](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)、[HN 討論](https://news.ycombinator.com/item?id=47899844)。

---

## P1 — 核心缺口

### 1. 跨文件主動回寫（Active Back-linking）

**現況：** 新文件 ingest 只產出自己的頁面，靠同 slug upsert 合併。舊頁面不會被回頭修改加新連結。

**目標：** ingest 完成後，LLM 掃描「本次新產頁面」vs「既有 wiki 全頁清單」，找出應補 cross-reference 的舊頁，批次 upsert 更新 `links_to`。

**設計重點：**
- 僅在 ingest 成功後觸發，作為 post-ingest pass
- 掃描範圍：同 api_key_id 的全部既有頁面（不限數量，正確性優先）
- LLM 輸出：`{ slug: string, add_links_to: string[] }[]`（只補連結，不改內容）

**Karpathy 原文：** 「automatically updates 10-15 related wiki pages」— 這是原版核心，目前未實作。

---

### 2. LINE 對話 → Wiki（Chat-to-Wiki）

**現況：** `run_query` 有 `save_to_wiki` 參數，LINE bot 呼叫時未傳（預設 False）。

**目標：** LINE bot 根據 `route_query` 結果 + wiki 管理員 LLM 自評，決定是否存回 wiki：
- `need_wiki=False`（閒聊）→ 直接丟棄，不需進入評估
- `need_wiki=True` → 交給「wiki 管理員」評估品質與增量價值

#### Wiki 管理員設計

wiki 管理員是獨立的 LLM 呼叫，在回答完成後非同步執行。它必須內建 wiki 的核心理念，避免錯誤內容破壞知識庫方向。

**管理員的 wiki 靈魂 system prompt 要包含：**
- wiki 是「蒸餾後的結構化知識」，不是對話存檔
- 每頁有明確 entity/concept，互相交叉連結，追求知識複利累積
- 來源是使用者主動餵入的文件；聊天只能「補充」，不能「主導」wiki 方向
- 管理員的職責是守門，不是照單全收

**值得存回的三種情況（任一滿足）：**
1. 答案揭露既有頁面之間的**新連結**（A 和 B 原本沒有 cross-reference，但答案說明了關係）
2. 答案對某個既有頁面有**實質補充**（新 context / 細節 / 修正，且與原頁不重複）
3. 答案合成出一個**還沒有獨立頁面的新概念**，且概念足夠獨立、普適

**硬性拒絕條件（任一命中即拒）：**
- 答案只是重述現有 wiki 頁面內容，無增量價值
- 低信心 / 模糊推測（「可能是...」「不太確定...」）
- 問題屬個人事務 / 閒聊 / 不屬知識庫主題
- 答案與 wiki 的既有知識方向相悖（可能是錯誤資訊）

**管理員輸出結構：**

```python
class WikiSaveDecision(BaseModel):
    worth_saving: bool
    reason: str                          # 給 log 用，不顯示給使用者
    save_type: Literal["new_page", "supplement", "new_links"] | None
    target_slug: str | None              # supplement / new_links 時指向既有頁面
    new_content: str | None             # 合成後的 wiki 格式內容（非原始問答）
    add_links: list[str]                 # 應補的跨頁連結 slug
```

**存回格式：**
- 頁面類型：`query`（新頁）或直接 upsert 既有頁（supplement）
- 內容：管理員合成的 wiki 格式，不是原始問答文字
- Slug：`query/{timestamp}-{hash}`（新頁時）

---

## P2 — 品質提升

### 3. LINE 路由優化（route_query 過濾冗贅）

**現況：** 所有 LINE 訊息都走完整 `run_query`（含 wiki 搜尋），即使是純閒聊也一樣。

**目標：** `route_query` 回傳 `need_wiki=False` 時，直接走 `chat_only_reply`，跳過 wiki 搜尋。

**效益：**
- 省 token（不搜 wiki）
- 省時間（少一次 embedding 搜尋）
- 自然成為 Chat-to-Wiki 的篩選閘門（只有 wiki 相關對話才考慮存回）

**實作位置：** `linebot.py` `run_query` 呼叫處，改為先 `route_query` 再分支。

---

### 4. Lint 自動排程

**現況：** Lint 手動觸發。

**目標：** 定期自動執行，偵測並標記問題。

**兩層設計（社群共識）：**
1. **Programmatic 層**（秒級）：死連結、孤立頁、格式違規 — 純 DB/regex 掃描，不需 LLM
2. **LLM 層**（分鐘級）：語意矛盾、過期聲明、缺少交叉引用 — 按 api_key_id 逐批執行

**觸發方式：** 後端 background task（每日一次）或 admin 手動觸發（現有）+ 排程

---

## P3 — 進階

### 5. Lint + Web Search（補缺口驗證）

**現況：** Lint 只做靜態分析。

**目標：** Lint 掃到「過期聲明」時，可選擇性 web search 驗證。

**Karpathy 原文：** Lint 時 web search 是 optional，用來「fill data gaps」。

---

### 6. Typed Relationships（語意關係連結）

**現況：** wiki 頁面之間只有純 `[[wikilink]]`。

**目標：** 加帶語意的關係類型，例如：
- `depends-on`、`supersedes`、`implements`、`owned-by`

**社群 v2 建議：** 500+ 頁後純 wikilink 難以維護；typed links 提升圖譜可用性。

**目前規模不急，列為追蹤。**

---

## 優先順序

| 順序 | 項目 | 理由 |
|------|------|------|
| 1 | #3 LINE 路由優化 | 改動小（linebot.py 一處），立即省 token |
| 2 | #2 Chat-to-Wiki | 依賴 #3 的路由結果，一起做最省事 |
| 3 | #1 跨文件主動回寫 | 核心缺口，需設計 post-ingest LLM pass |
| 4 | #4 Lint 排程 | 需 background scheduler 機制 |
| 5 | #5 Lint + Web Search | 依賴 #4 完成後擴充 |
| 6 | #6 Typed Relationships | 規模未到，暫緩 |

---

## 參考資料

- [Karpathy 原始 gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [LLM Wiki v2 with typed relationships](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)
- [18 architectural extensions](https://gist.github.com/redmizt/968165ae7f1a408b0e60af02d68b90b6)
- [HN 討論](https://news.ycombinator.com/item?id=47899844)
- [Beyond RAG 分析](https://levelup.gitconnected.com/beyond-rag-how-andrej-karpathys-llm-wiki-pattern-builds-knowledge-that-actually-compounds-31a08528665e)
