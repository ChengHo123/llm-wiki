import axios from 'axios'

const API_KEY_STORAGE = 'llm_wiki_api_key'
const KEY_LIST_STORAGE = 'llm_wiki_key_list'
const ACTIVE_KEY_NAME_STORAGE = 'llm_wiki_active_key_name'

export interface StoredKey {
  name: string
  key: string
}

export function listStoredKeys(): StoredKey[] {
  try {
    const raw = localStorage.getItem(KEY_LIST_STORAGE)
    if (!raw) return []
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr.filter((x) => x?.name && x?.key) : []
  } catch {
    return []
  }
}

function saveKeyList(list: StoredKey[]) {
  localStorage.setItem(KEY_LIST_STORAGE, JSON.stringify(list))
}

export function getActiveKeyName(): string {
  return localStorage.getItem(ACTIVE_KEY_NAME_STORAGE) || ''
}

export function getStoredApiKey(): string {
  return localStorage.getItem(API_KEY_STORAGE) || ''
}

export function setStoredApiKey(key: string) {
  localStorage.setItem(API_KEY_STORAGE, key)
}

export function addStoredKey(name: string, key: string) {
  const list = listStoredKeys()
  const idx = list.findIndex((k) => k.name === name)
  if (idx >= 0) list[idx] = { name, key }
  else list.push({ name, key })
  saveKeyList(list)
  selectStoredKey(name)
}

export function selectStoredKey(name: string): boolean {
  const entry = listStoredKeys().find((k) => k.name === name)
  if (!entry) return false
  localStorage.setItem(API_KEY_STORAGE, entry.key)
  localStorage.setItem(ACTIVE_KEY_NAME_STORAGE, name)
  return true
}

export function removeStoredKey(name: string) {
  const list = listStoredKeys().filter((k) => k.name !== name)
  saveKeyList(list)
  if (getActiveKeyName() === name) {
    localStorage.removeItem(API_KEY_STORAGE)
    localStorage.removeItem(ACTIVE_KEY_NAME_STORAGE)
  }
}

export function clearStoredApiKey() {
  localStorage.removeItem(API_KEY_STORAGE)
  localStorage.removeItem(ACTIVE_KEY_NAME_STORAGE)
}

const api = axios.create({
  baseURL: '/api/v1',
})

function isSessionToken(value: string): boolean {
  return value.startsWith('ws_')
}

api.interceptors.request.use((config) => {
  const key = getStoredApiKey()
  if (key) {
    if (isSessionToken(key)) {
      config.headers['X-Session-Token'] = key
    } else {
      config.headers['X-API-Key'] = key
    }
  }
  return config
})

// ── Auth ────────────────────────────────────────────────
export async function createApiKey(name: string) {
  const res = await api.post('/keys', { name })
  return res.data as { key: string; name: string; message: string }
}

export async function whoAmI(rawKey: string): Promise<{ name: string }> {
  const res = await axios.get('/api/v1/keys/me', {
    headers: { 'X-API-Key': rawKey },
  })
  return res.data
}

// ── Documents ───────────────────────────────────────────
export interface Document {
  id: string
  filename: string
  content_type: string
  status: string
  error_message: string | null
  created_at: string
}

export async function uploadDocument(file: File): Promise<Document> {
  const form = new FormData()
  form.append('file', file)
  const res = await api.post('/documents', form)
  return res.data
}

export async function listDocuments(): Promise<Document[]> {
  const res = await api.get('/documents')
  return res.data
}

export async function retryDocument(id: string): Promise<Document> {
  const res = await api.post(`/documents/${id}/retry`)
  return res.data
}

export async function deleteDocument(id: string, deletePages = true): Promise<{ deleted_document_id: string; pages_deleted: number }> {
  const res = await api.delete(`/documents/${id}`, { params: { delete_pages: deletePages } })
  return res.data
}

export async function deleteWikiPage(id: string): Promise<void> {
  await api.delete(`/wiki/pages/${id}`)
}

// ── Wiki ────────────────────────────────────────────────
export interface WikiPageSummary {
  id: string
  title: string
  slug: string
  page_type: string
  updated_at: string
}

export interface WikiPageDetail extends WikiPageSummary {
  content: string
  created_at: string
}

export interface GraphData {
  nodes: { id: string; title: string; slug: string; page_type: string }[]
  edges: { source: string; target: string; link_text: string | null }[]
}

export async function listWikiPages(): Promise<WikiPageSummary[]> {
  const res = await api.get('/wiki/pages')
  return res.data
}

export async function getWikiPage(id: string): Promise<WikiPageDetail> {
  const res = await api.get(`/wiki/pages/${id}`)
  return res.data
}

export async function getWikiGraph(): Promise<GraphData> {
  const res = await api.get('/wiki/graph')
  return res.data
}

export async function lintWiki() {
  const res = await api.post('/wiki/lint')
  return res.data
}

export interface LintIssue {
  type?: string
  severity?: string
  page_slug: string
  description?: string
  suggestion?: string
}

export interface LintApplyResult {
  applied: { page_slug: string; page_id: string; title: string; issues_addressed: number }[]
  skipped: { page_slug: string; reason: string }[]
}

export async function applyLintFixes(issues: LintIssue[]): Promise<LintApplyResult> {
  const res = await api.post('/wiki/lint/apply', { issues })
  return res.data
}

// ── Query ───────────────────────────────────────────────
export interface QueryResult {
  answer: string
  referenced_pages: { id: string; title: string; slug: string }[]
  saved_page: { id: string; title: string; slug: string } | null
}

export async function queryWiki(question: string, saveToWiki: boolean): Promise<QueryResult> {
  const res = await api.post('/query', { question, save_to_wiki: saveToWiki })
  return res.data
}

export interface RefineEdit {
  action: 'update' | 'create'
  slug: string
  title: string
  page_type: 'entity' | 'concept'
  reason: string
}

export type QueryStreamEvent =
  | { type: 'route'; need_wiki: boolean; reason: string }
  | { type: 'pages'; pages: { id: string; title: string; slug: string }[] }
  | { type: 'chunk'; content: string }
  | { type: 'judge'; save: boolean; reason: string }
  | { type: 'refine'; edits: RefineEdit[]; summary: string }
  | { type: 'done' }
  | { type: 'error'; message: string }

export interface ChatHistoryMsg {
  role: 'user' | 'assistant'
  content: string
}

// ── Admin ───────────────────────────────────────────────
const adminApi = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
})

export interface AdminUserSummary {
  api_key_id: string
  name: string
  line_user_id: string | null
  end_user_tag: string
  created_at: string
  document_count: number
  wiki_page_count: number
  in_progress_count: number
  chat_count: number
}

export interface AdminUserDetail {
  summary: AdminUserSummary
  documents: Document[]
}

export interface AdminKpi {
  total_users: number
  dau: number
  wau: number
  mau: number
  new_users_this_week: number
  new_users_last_week: number
  total_documents: number
  total_wiki_pages: number
  queue_depth: number
  range_ingest_total: number
  range_ingest_done: number
  range_ingest_error: number
  range_success_rate: number | null
  range_query_count: number
}

export interface AdminRange {
  start: string  // YYYY-MM-DD
  end: string
  days: number
  granularity?: string  // "day" | "hour"
}

export interface AdminLeaderEntry {
  api_key_id: string
  name: string
  line_user_id: string | null
  value: number
}

export interface AdminTrendPoint {
  date: string  // YYYY-MM-DD
  ingest_done: number
  ingest_error: number
  ingest_total: number
  query_count: number
}

export interface AdminOverview {
  range: AdminRange
  kpi: AdminKpi
  top_uploaders: AdminLeaderEntry[]
  top_queriers: AdminLeaderEntry[]
  top_wiki: AdminLeaderEntry[]
  trends: AdminTrendPoint[]
}

export async function adminLogin(username: string, password: string): Promise<void> {
  await adminApi.post('/admin/login', { username, password })
}

export async function adminLogout(): Promise<void> {
  await adminApi.post('/admin/logout')
}

export async function adminMe(): Promise<{ ok: boolean }> {
  const res = await adminApi.get('/admin/me')
  return res.data
}

export async function adminListUsers(): Promise<AdminUserSummary[]> {
  const res = await adminApi.get('/admin/users')
  return res.data
}

export async function adminOverview(start?: string, end?: string): Promise<AdminOverview> {
  const res = await adminApi.get('/admin/overview', {
    params: { start_date: start, end_date: end },
  })
  return res.data
}

export interface AdminSpendUser {
  api_key_id: string | null
  name: string
  line_user_id: string | null
  end_user_tag: string
  call_count: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  spend_usd: number
}

export interface AdminSpendModel {
  model: string
  call_count: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  spend_usd: number
}

export interface AdminTokenTrendSeries {
  api_key_id: string | null
  name: string
  end_user_tag: string
  total_tokens: number
  daily_tokens: number[]  // 與 AdminTokenTrendOut.dates 同長同序
}

export interface AdminTokenTrendOut {
  dates: string[]               // YYYY-MM-DD
  total_daily: number[]         // 每日全平台 total tokens
  top_users: AdminTokenTrendSeries[]
}

export interface AdminSpend {
  range: AdminRange
  total_call_count: number
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  total_spend_usd: number
  untagged_call_count: number
  untagged_tokens: number
  by_user: AdminSpendUser[]
  by_model: AdminSpendModel[]
  trends: AdminTokenTrendOut
  fetched_count: number
  note: string | null
}

export async function adminSpend(start?: string, end?: string): Promise<AdminSpend> {
  const res = await adminApi.get('/admin/spend', {
    params: { start_date: start, end_date: end },
  })
  return res.data
}

export async function adminUserDetail(apiKeyId: string): Promise<AdminUserDetail> {
  const res = await adminApi.get(`/admin/users/${apiKeyId}`)
  return res.data
}

export async function adminRetryDocument(documentId: string): Promise<void> {
  await adminApi.post(`/admin/documents/${documentId}/retry`)
}

export interface LogEntry {
  time: string
  level: string
  logger: string
  message: string
}

export async function adminLogs(n: number = 200): Promise<LogEntry[]> {
  const res = await adminApi.get('/admin/logs', { params: { n } })
  return res.data
}

export async function* queryWikiStream(
  question: string,
  history: ChatHistoryMsg[] = [],
): AsyncGenerator<QueryStreamEvent> {
  const credential = getStoredApiKey()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (credential) {
    if (isSessionToken(credential)) headers['X-Session-Token'] = credential
    else headers['X-API-Key'] = credential
  }
  const res = await fetch('/api/v1/query/stream', {
    method: 'POST',
    headers,
    body: JSON.stringify({ question, history }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  if (!res.body) throw new Error('No response body')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.trim()) continue
      try {
        yield JSON.parse(line) as QueryStreamEvent
      } catch {
        // 忽略非法 JSON 行
      }
    }
  }
  if (buffer.trim()) {
    try { yield JSON.parse(buffer) as QueryStreamEvent } catch {}
  }
}
