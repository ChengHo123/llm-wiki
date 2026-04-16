import axios from 'axios'

const API_KEY_STORAGE = 'llm_wiki_api_key'

export function getStoredApiKey(): string {
  return localStorage.getItem(API_KEY_STORAGE) || ''
}

export function setStoredApiKey(key: string) {
  localStorage.setItem(API_KEY_STORAGE, key)
}

export function clearStoredApiKey() {
  localStorage.removeItem(API_KEY_STORAGE)
}

const api = axios.create({
  baseURL: '/api/v1',
})

api.interceptors.request.use((config) => {
  const key = getStoredApiKey()
  if (key) config.headers['X-API-Key'] = key
  return config
})

// ── Auth ────────────────────────────────────────────────
export async function createApiKey(name: string) {
  const res = await api.post('/keys', { name })
  return res.data as { key: string; name: string; message: string }
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
