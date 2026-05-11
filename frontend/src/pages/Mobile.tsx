import { useState, useEffect, useCallback, useRef } from 'react'
import { useDropzone } from 'react-dropzone'
import ForceGraph2D from 'react-force-graph-2d'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Upload, FileText, CheckCircle, XCircle, Clock, RefreshCw, Trash2, RotateCcw,
  Network, ArrowLeft, LogOut, BookOpen, ChevronRight,
} from 'lucide-react'
import {
  uploadDocument, listDocuments, deleteDocument, retryDocument,
  getStoredApiKey, clearStoredApiKey, getActiveKeyName,
  getWikiGraph, listWikiPages, getWikiPage, deleteWikiPage,
  type Document, type GraphData, type WikiPageSummary, type WikiPageDetail,
} from '../api/client'

const STATUS_ICON: Record<string, JSX.Element> = {
  done: <CheckCircle size={16} className="text-green-500" />,
  error: <XCircle size={16} className="text-red-500" />,
  processing: <RefreshCw size={16} className="text-blue-500 animate-spin" />,
  queued: <Clock size={16} className="text-yellow-400" />,
  pending: <Clock size={16} className="text-gray-400" />,
}

const STATUS_LABEL: Record<string, string> = {
  done: '完成',
  error: '失敗',
  processing: '處理中',
  queued: '排隊中',
  pending: '等待中',
}

const PAGE_TYPE_COLOR: Record<string, string> = {
  index:   '#8b5cf6',
  summary: '#3b82f6',
  entity:  '#10b981',
  concept: '#f59e0b',
}

const PAGE_TYPE_BADGE: Record<string, string> = {
  index:   'bg-purple-100 text-purple-700',
  summary: 'bg-blue-100 text-blue-700',
  entity:  'bg-green-100 text-green-700',
  concept: 'bg-orange-100 text-orange-700',
}

const PAGE_TYPE_LABEL: Record<string, string> = {
  index:   '索引',
  summary: '摘要',
  entity:  '實體',
  concept: '概念',
}

type View = 'main' | 'graph' | 'wiki-list' | 'wiki-detail'

interface MobileGraphNode {
  id: string
  title: string
  page_type: string
  x?: number
  y?: number
}

function GraphView({ onBack, onOpenPage }: { onBack: () => void; onOpenPage: (id: string) => void }) {
  const [data, setData] = useState<{ nodes: MobileGraphNode[]; links: any[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [size, setSize] = useState({ w: window.innerWidth, h: window.innerHeight - 56 })
  const fgRef = useRef<any>(null)

  useEffect(() => {
    const onResize = () => setSize({ w: window.innerWidth, h: window.innerHeight - 56 })
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    let alive = true
    setLoading(true)
    getWikiGraph()
      .then((g: GraphData) => {
        if (!alive) return
        setData({
          nodes: g.nodes as MobileGraphNode[],
          links: g.edges.map((e) => ({ source: e.source, target: e.target })),
        })
      })
      .catch(() => alive && setError('載入圖譜失敗'))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [])

  const nodeCanvasObject = useCallback((node: MobileGraphNode, ctx: CanvasRenderingContext2D, scale: number) => {
    const r = Math.max(5, 8 / Math.sqrt(scale))
    ctx.beginPath()
    ctx.arc(node.x!, node.y!, r, 0, 2 * Math.PI)
    ctx.fillStyle = PAGE_TYPE_COLOR[node.page_type] || '#6b7280'
    ctx.fill()
    if (scale > 0.7) {
      const fs = Math.max(10, 11 / scale)
      ctx.font = `${fs}px sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      const w = ctx.measureText(node.title).width
      ctx.fillStyle = 'rgba(255,255,255,0.85)'
      ctx.fillRect(node.x! - w / 2 - 2, node.y! + r + 2, w + 4, fs + 2)
      ctx.fillStyle = '#374151'
      ctx.fillText(node.title, node.x!, node.y! + r + 3)
    }
  }, [])

  return (
    <div className="fixed inset-0 z-30 bg-gray-50 dark:bg-zinc-950 flex flex-col">
      <header className="h-14 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700 flex items-center px-3 gap-2">
        <button onClick={onBack} className="p-2 -ml-2 text-gray-600 dark:text-zinc-400">
          <ArrowLeft size={20} />
        </button>
        <h1 className="font-semibold text-gray-800 dark:text-zinc-100">知識圖譜</h1>
        {data && (
          <span className="text-xs text-gray-400 dark:text-zinc-500 ml-auto">
            {data.nodes.length} 頁 · {data.links.length} 連結
          </span>
        )}
      </header>
      <div className="flex-1 relative">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center">
            <RefreshCw size={28} className="animate-spin text-blue-400" />
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-red-500 text-sm">{error}</p>
          </div>
        )}
        {!loading && data && data.nodes.length === 0 && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-400 dark:text-zinc-500 px-6 text-center">
            <Network size={36} className="mb-2 opacity-30" />
            <p className="text-sm">尚無 wiki 頁面，先上傳文件給嚕比</p>
          </div>
        )}
        {data && data.nodes.length > 0 && (
          <ForceGraph2D
            ref={fgRef}
            graphData={data}
            nodeId="id"
            nodeLabel="title"
            nodeCanvasObject={nodeCanvasObject}
            nodeCanvasObjectMode={() => 'replace'}
            linkColor={() => 'rgba(156,163,175,0.6)'}
            linkWidth={1}
            linkDirectionalArrowLength={4}
            linkDirectionalArrowRelPos={1}
            backgroundColor="#f9fafb"
            width={size.w}
            height={size.h}
            cooldownTicks={100}
            onEngineStop={() => fgRef.current?.zoomToFit(400, 30)}
            onNodeClick={(n: any) => onOpenPage(n.id)}
          />
        )}
      </div>
    </div>
  )
}

function WikiListView({
  onBack, onOpenPage,
}: {
  onBack: () => void
  onOpenPage: (id: string) => void
}) {
  const [pages, setPages] = useState<WikiPageSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('')

  useEffect(() => {
    let alive = true
    setLoading(true)
    listWikiPages()
      .then((p) => alive && setPages(p))
      .catch(() => alive && setError('載入失敗'))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [])

  const filtered = filter.trim()
    ? pages.filter((p) => p.title.toLowerCase().includes(filter.toLowerCase()))
    : pages

  return (
    <div className="fixed inset-0 z-30 bg-gray-50 dark:bg-zinc-950 flex flex-col">
      <header className="h-14 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700 flex items-center px-3 gap-2 shrink-0">
        <button onClick={onBack} className="p-2 -ml-2 text-gray-600 dark:text-zinc-400">
          <ArrowLeft size={20} />
        </button>
        <h1 className="font-semibold text-gray-800 dark:text-zinc-100">Wiki 頁面</h1>
        <span className="text-xs text-gray-400 dark:text-zinc-500 ml-auto">{pages.length} 頁</span>
      </header>

      <div className="px-4 py-3 bg-white dark:bg-zinc-900 border-b border-gray-100 dark:border-zinc-800 shrink-0">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="搜尋標題…"
          className="w-full border border-gray-200 dark:border-zinc-600 bg-white dark:bg-zinc-800 text-gray-800 dark:text-zinc-100 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 dark:focus:ring-blue-700 placeholder:text-gray-400 dark:placeholder:text-zinc-500"
        />
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {loading ? (
          <div className="flex justify-center py-16">
            <RefreshCw size={24} className="animate-spin text-gray-400 dark:text-zinc-500" />
          </div>
        ) : error ? (
          <p className="text-sm text-red-500 text-center py-16">{error}</p>
        ) : filtered.length === 0 ? (
          <div className="text-center py-16 text-gray-400 dark:text-zinc-500">
            <BookOpen size={36} className="mx-auto mb-2 opacity-30" />
            <p className="text-sm">{pages.length === 0 ? '還沒有 wiki 頁面' : '沒有符合的頁面'}</p>
          </div>
        ) : (
          <ul className="space-y-2">
            {filtered.map((page) => (
              <li key={page.id}>
                <button
                  onClick={() => onOpenPage(page.id)}
                  className="w-full bg-white dark:bg-zinc-900 border border-gray-200 dark:border-zinc-700 rounded-2xl px-4 py-3 text-left active:bg-gray-50 dark:active:bg-zinc-800 flex items-start gap-2"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${PAGE_TYPE_BADGE[page.page_type] || 'bg-gray-100 text-gray-600'}`}>
                        {PAGE_TYPE_LABEL[page.page_type] || page.page_type}
                      </span>
                      <span className="text-[10px] text-gray-400 dark:text-zinc-500 ml-auto">
                        {new Date(page.updated_at).toLocaleDateString('zh-TW')}
                      </span>
                    </div>
                    <h3 className="font-medium text-gray-800 dark:text-zinc-200 text-sm leading-snug">{page.title}</h3>
                  </div>
                  <ChevronRight size={16} className="text-gray-300 dark:text-zinc-600 flex-shrink-0 mt-1" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function WikiDetailView({
  pageId, onBack, onOpenPage,
}: {
  pageId: string
  onBack: () => void
  onOpenPage: (id: string) => void
}) {
  const [page, setPage] = useState<WikiPageDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [allPages, setAllPages] = useState<WikiPageSummary[]>([])

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    setPage(null)
    Promise.all([getWikiPage(pageId), listWikiPages()])
      .then(([p, list]) => {
        if (!alive) return
        setPage(p)
        setAllPages(list)
      })
      .catch(() => alive && setError('載入頁面失敗'))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [pageId])

  // [[標題]] 轉成可點擊連結
  const renderContent = (text: string) => {
    return text.replace(
      /\[\[([^\]]+?)(?:\|([^\]]+?))?\]\]/g,
      (_, target, alias) => {
        const display = alias || target
        const found = allPages.find((p) => p.title === target || p.slug === target)
        if (found) return `[${display}](#wiki:${found.id})`
        return display
      },
    )
  }

  const handleDelete = async () => {
    if (!page) return
    if (!confirm(`刪除頁面「${page.title}」？`)) return
    try {
      await deleteWikiPage(page.id)
      onBack()
    } catch {
      setError('刪除失敗')
    }
  }

  return (
    <div className="fixed inset-0 z-30 bg-gray-50 dark:bg-zinc-950 flex flex-col">
      <header className="h-14 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700 flex items-center px-3 gap-2 shrink-0">
        <button onClick={onBack} className="p-2 -ml-2 text-gray-600 dark:text-zinc-400">
          <ArrowLeft size={20} />
        </button>
        <h1 className="font-semibold text-gray-800 dark:text-zinc-100 flex-1 truncate">
          {page?.title || 'Wiki 頁面'}
        </h1>
        {page && (
          <button
            onClick={handleDelete}
            className="p-2 -mr-2 text-gray-300 dark:text-zinc-600 active:text-red-500"
            title="刪除"
          >
            <Trash2 size={16} />
          </button>
        )}
      </header>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex justify-center py-16">
            <RefreshCw size={24} className="animate-spin text-gray-400 dark:text-zinc-500" />
          </div>
        ) : error || !page ? (
          <p className="text-sm text-red-500 text-center py-16">{error || '頁面不存在'}</p>
        ) : (
          <article className="px-4 py-4 max-w-md mx-auto">
            <div className="flex items-center gap-2 mb-2">
              <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${PAGE_TYPE_BADGE[page.page_type] || 'bg-gray-100 text-gray-600'}`}>
                {PAGE_TYPE_LABEL[page.page_type] || page.page_type}
              </span>
              <span className="text-[10px] text-gray-400 dark:text-zinc-500">
                更新：{new Date(page.updated_at).toLocaleDateString('zh-TW')}
              </span>
            </div>
            <h1 className="text-xl font-bold text-gray-800 dark:text-zinc-100 mb-3 leading-tight">{page.title}</h1>
            <div className="prose prose-base dark:prose-invert max-w-none break-words [&_pre]:overflow-x-auto [&_pre]:whitespace-pre-wrap [&_img]:max-w-full [&_table]:block [&_table]:overflow-x-auto">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ href, children, ...rest }) => {
                    if (href?.startsWith('#wiki:')) {
                      const id = href.slice(6)
                      return (
                        <button
                          type="button"
                          onClick={() => onOpenPage(id)}
                          className="text-blue-600 dark:text-blue-400 underline"
                        >
                          {children}
                        </button>
                      )
                    }
                    return <a href={href} target="_blank" rel="noreferrer" {...rest}>{children}</a>
                  },
                }}
              >
                {renderContent(page.content)}
              </ReactMarkdown>
            </div>
          </article>
        )}
      </div>
    </div>
  )
}

export default function MobilePage() {
  const [apiKey, setApiKey] = useState(getStoredApiKey())
  const [activeName, setActiveName] = useState(getActiveKeyName())
  const [docs, setDocs] = useState<Document[]>([])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState<View>('main')
  const [selectedPageId, setSelectedPageId] = useState<string | null>(null)

  const loadDocs = useCallback(async () => {
    if (!getStoredApiKey()) return
    try {
      setDocs(await listDocuments())
    } catch {}
  }, [])

  useEffect(() => { loadDocs() }, [loadDocs])

  useEffect(() => {
    const hasPending = docs.some((d) => ['processing', 'pending', 'queued'].includes(d.status))
    if (!hasPending) return
    const t = setInterval(loadDocs, 3000)
    return () => clearInterval(t)
  }, [docs, loadDocs])

  const onDrop = useCallback(async (files: File[]) => {
    if (!getStoredApiKey()) { setError('請從 LINE 點「取得連結」進入'); return }
    setUploading(true)
    setError('')
    try {
      for (const file of files) await uploadDocument(file)
      await loadDocs()
    } catch (e: any) {
      setError(e.response?.data?.detail || '上傳失敗')
    } finally {
      setUploading(false)
    }
  }, [loadDocs])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'image/*': ['.png', '.jpg', '.jpeg', '.gif', '.webp'],
      'text/plain': ['.txt'],
      'text/markdown': ['.md'],
    },
  })

  const handleLogout = () => {
    if (!confirm('登出後此瀏覽器會清空登入狀態，需重新從 LINE 取得連結。')) return
    clearStoredApiKey()
    setApiKey('')
    setActiveName('')
    setDocs([])
  }

  const openPage = (id: string) => {
    setSelectedPageId(id)
    setView('wiki-detail')
  }

  if (!apiKey) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-zinc-950 flex flex-col items-center justify-center p-6 text-center">
        <Network size={42} className="text-blue-400 mb-3 opacity-60" />
        <h1 className="text-lg font-semibold text-gray-700 dark:text-zinc-200 mb-2">嚕比的 wiki</h1>
        <p className="text-sm text-gray-500 dark:text-zinc-400">
          請從 LINE 傳「取得連結」訊息給嚕比，<br />
          點 bot 回的網址進來。
        </p>
      </div>
    )
  }

  if (view === 'graph') {
    return <GraphView onBack={() => setView('main')} onOpenPage={openPage} />
  }
  if (view === 'wiki-list') {
    return <WikiListView onBack={() => setView('main')} onOpenPage={openPage} />
  }
  if (view === 'wiki-detail' && selectedPageId) {
    return (
      <WikiDetailView
        pageId={selectedPageId}
        onBack={() => setView('wiki-list')}
        onOpenPage={openPage}
      />
    )
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-zinc-950">
      <header className="sticky top-0 z-10 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700 px-4 h-14 flex items-center">
        <h1 className="font-semibold text-gray-800 dark:text-zinc-100 flex-1 truncate">嚕比的 wiki</h1>
        <span className="text-xs text-gray-400 dark:text-zinc-500 mr-2 truncate max-w-[120px]">{activeName}</span>
        <button onClick={handleLogout} className="p-2 -mr-2 text-gray-400 dark:text-zinc-500 hover:text-red-500" title="登出">
          <LogOut size={18} />
        </button>
      </header>

      <main className="px-4 py-4 space-y-4 max-w-md mx-auto">
        <section>
          <div
            {...getRootProps()}
            className={`border-2 border-dashed rounded-2xl py-10 px-4 text-center transition-colors ${
              isDragActive
                ? 'border-blue-400 bg-blue-50 dark:bg-blue-950'
                : 'border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-900'
            }`}
          >
            <input {...getInputProps()} />
            <Upload size={32} className="mx-auto mb-3 text-gray-400 dark:text-zinc-500" />
            {uploading ? (
              <p className="text-blue-600 dark:text-blue-400 font-medium">上傳中…</p>
            ) : (
              <>
                <p className="text-gray-700 dark:text-zinc-200 font-medium mb-1">點擊或拖放上傳</p>
                <p className="text-xs text-gray-400 dark:text-zinc-500">PDF、圖片、TXT、Markdown</p>
              </>
            )}
          </div>
        </section>

        <section className="bg-white dark:bg-zinc-900 rounded-2xl border border-gray-200 dark:border-zinc-700 p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold text-gray-700 dark:text-zinc-200 text-sm">已上傳 ({docs.length})</h2>
            <button onClick={loadDocs} className="text-gray-400 dark:text-zinc-500 active:text-gray-600 dark:active:text-zinc-300 p-1">
              <RefreshCw size={14} />
            </button>
          </div>
          {docs.length === 0 ? (
            <p className="text-sm text-gray-400 dark:text-zinc-500 text-center py-6">還沒上傳任何文件</p>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-zinc-800">
              {docs.map((doc) => (
                <li key={doc.id} className="flex items-center gap-2 py-2.5">
                  <FileText size={16} className="text-gray-400 dark:text-zinc-500 flex-shrink-0" />
                  <span className="flex-1 text-sm text-gray-700 dark:text-zinc-200 truncate">{doc.filename}</span>
                  <div className="flex items-center gap-1 text-xs text-gray-500 dark:text-zinc-400 flex-shrink-0">
                    {STATUS_ICON[doc.status] ?? <Clock size={16} />}
                    <span>{STATUS_LABEL[doc.status] ?? doc.status}</span>
                  </div>
                  {doc.status === 'error' && (
                    <button
                      onClick={async () => {
                        try { await retryDocument(doc.id); await loadDocs() }
                        catch { setError('重試失敗') }
                      }}
                      className="text-gray-300 dark:text-zinc-600 active:text-blue-500 p-1 -mr-1"
                      title="重試"
                    >
                      <RotateCcw size={15} />
                    </button>
                  )}
                  <button
                    onClick={async () => {
                      if (!confirm(`刪除「${doc.filename}」及其 wiki 頁面？`)) return
                      try { await deleteDocument(doc.id); await loadDocs() }
                      catch { setError('刪除失敗') }
                    }}
                    className="text-gray-300 dark:text-zinc-600 active:text-red-500 p-1 -mr-1"
                    title="刪除"
                  >
                    <Trash2 size={15} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <button
          onClick={() => setView('wiki-list')}
          className="w-full bg-white dark:bg-zinc-900 border border-gray-200 dark:border-zinc-700 rounded-2xl py-4 flex items-center justify-center gap-2 text-gray-700 dark:text-zinc-200 font-medium active:bg-gray-50 dark:active:bg-zinc-800"
        >
          <BookOpen size={18} className="text-blue-500" />
          看 Wiki 頁面
        </button>

        <button
          onClick={() => setView('graph')}
          className="w-full bg-white dark:bg-zinc-900 border border-gray-200 dark:border-zinc-700 rounded-2xl py-4 flex items-center justify-center gap-2 text-gray-700 dark:text-zinc-200 font-medium active:bg-gray-50 dark:active:bg-zinc-800"
        >
          <Network size={18} className="text-blue-500" />
          看知識圖譜
        </button>

        {error && (
          <p className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 rounded-lg px-3 py-2">{error}</p>
        )}
      </main>
    </div>
  )
}
