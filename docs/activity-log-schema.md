# ActivityLog `details` schema

`activity_log.details` 是 JSONB，依 `action` 欄位的值會有不同結構。本文記錄每個 action 預期的鍵與型別。

新增 action 時請同步更新此文件。

## action: `ingest`

```json
{
  "chunked": false,
  "summary": "本文件主要說明 ...",
  "filename": "report.pdf",
  "document_id": "uuid",
  "pages_created": 6,
  "back_link_edits": [
    { "target_slug": "...", "title": "...", "reason": "..." }
  ]
}
```

## action: `query`

```json
{
  "pages_referenced": 12,
  "save_decision": true,
  "edits_applied": [
    { "page_id": "uuid", "slug": "...", "action": "update" }
  ]
}
```

## action: `chat`

純閒聊（`route_query` 判 `need_wiki=False`）。

```json
{}
```

## action: `lint`

```json
{
  "total_pages": 180,
  "issues_found": 5
}
```

## action: `lint_apply`

```json
{
  "requested": 5,
  "applied_pages": 3,
  "skipped": 2
}
```

## action: `document_delete`

```json
{
  "document_id": "uuid",
  "filename": "report.pdf",
  "pages_deleted": 2,
  "delete_pages_flag": true
}
```

## action: `wiki_page_delete`

```json
{
  "page_id": "uuid",
  "slug": "海門玻璃",
  "title": "海門玻璃",
  "page_type": "entity"
}
```

## 設計筆記

- 不存 question / 答案原文 / 個資（避免外洩）
- 給後台統計 / 審計用；不該用 details 做 query 主索引
- 欄位可選新增；刪欄位視同 breaking change，需保留向下相容
