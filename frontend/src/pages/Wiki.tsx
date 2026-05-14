import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { BookOpen, ChevronLeft, ChevronRight, AlertTriangle, RefreshCw, Trash2, Wand2, Loader2 } from 'lucide-react'
import { listWikiPages, getWikiPage, lintWiki, deleteWikiPage, applyLintFixes, type WikiPageSummary, type WikiPageDetail, type LintIssue } from '../api/client'

const PAGE_TYPE_COLOR: Record<string, string> = {
  index: 'bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300',
  summary: 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300',
  entity: 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300',
  concept: 'bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300',
}

export default function WikiPage() {
  const [pages, setPages] = useState<WikiPageSummary[]>([])
  const [selected, setSelected] = useState<WikiPageDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [lintReport, setLintReport] = useState<any>(null)
  const [linting, setLinting] = useState(false)
  const [applying, setApplying] = useState<number | 'all' | null>(null)
  const [applyResult, setApplyResult] = useState<{ applied: number; skipped: number } | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    listWikiPages()
      .then(setPages)
      .catch(() => setError('載入失敗，請確認 API Key'))
      .finally(() => setLoading(false))
  }, [])

  const openPage = async (id: string) => {
    try {
      setSelected(await getWikiPage(id))
    } catch {
      setError('載入頁面失敗')
    }
  }

  const handleLint = async () => {
    setLinting(true)
    setApplyResult(null)
    try {
      setLintReport(await lintWiki())
    } catch {
      setError('Lint 執行失敗')
    } finally {
      setLinting(false)
    }
  }

  const runApply = async (issues: LintIssue[], key: number | 'all') => {
    if (!issues.length || applying !== null) return
    setApplying(key)
    setError('')
    try {
      const result = await applyLintFixes(issues)
      setApplyResult({ applied: result.applied.length, skipped: result.skipped.length })
      setPages(await listWikiPages())
      if (key === 'all') {
        setLintReport(null)
      } else if (lintReport?.issues) {
        setLintReport({
          ...lintReport,
          issues: lintReport.issues.filter((_: any, i: number) => i !== key),
          stats: { ...lintReport.stats, issues_found: Math.max(0, (lintReport.stats?.issues_found || 1) - 1) },
        })
      }
    } catch {
      setError('套用失敗')
    } finally {
      setApplying(null)
    }
  }

  const handleApplyAll = () => {
    if (!lintReport?.issues?.length) return
    if (!confirm(`將對 ${lintReport.issues.length} 個 issue 呼叫 LLM 批次改寫對應頁面，原內容會被覆寫。確定？`)) return
    runApply(lintReport.issues as LintIssue[], 'all')
  }

  if (selected) {
    return (
      <div className="p-8 max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <button
            onClick={() => setSelected(null)}
            className="flex items-center gap-1 text-sm text-gray-500 dark:text-zinc-400 hover:text-gray-700 dark:hover:text-zinc-200"
          >
            <ChevronLeft size={16} /> 返回列表
          </button>
          <button
            onClick={async () => {
              if (!confirm(`刪除頁面「${selected.title}」？`)) return
              try {
                await deleteWikiPage(selected.id)
                setSelected(null)
                setPages(await listWikiPages())
              } catch { setError('刪除失敗') }
            }}
            className="flex items-center gap-1 text-sm text-red-400 hover:text-red-600"
          >
            <Trash2 size={14} /> 刪除頁面
          </button>
        </div>
        <div className="flex items-center gap-2 mb-1">
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${PAGE_TYPE_COLOR[selected.page_type] || 'bg-gray-100 text-gray-600 dark:bg-zinc-800 dark:text-zinc-400'}`}>
            {selected.page_type}
          </span>
        </div>
        <h1 className="text-2xl font-bold text-gray-800 dark:text-zinc-100 mb-4">{selected.title}</h1>
        <div className="bg-white dark:bg-zinc-900 rounded-xl border border-gray-200 dark:border-zinc-700 p-6 prose dark:prose-invert max-w-none break-words">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected.content}</ReactMarkdown>
        </div>
        <p className="text-xs text-gray-400 dark:text-zinc-500 mt-3">最後更新：{new Date(selected.updated_at).toLocaleString('zh-TW')}</p>
        {(() => {
          const idx = pages.findIndex((p) => p.id === selected.id)
          const prev = idx > 0 ? pages[idx - 1] : null
          const next = idx >= 0 && idx < pages.length - 1 ? pages[idx + 1] : null
          return (
            <nav className="mt-6 flex gap-3">
              <button
                onClick={() => prev && openPage(prev.id)}
                disabled={!prev}
                className="flex-1 flex items-center gap-2 bg-white dark:bg-zinc-900 border border-gray-200 dark:border-zinc-700 rounded-xl px-4 py-3 text-left hover:bg-gray-50 dark:hover:bg-zinc-800 disabled:opacity-40 disabled:hover:bg-transparent transition-colors"
              >
                <ChevronLeft size={18} className="text-gray-400 dark:text-zinc-500 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-gray-400 dark:text-zinc-500">上一頁</div>
                  <div className="text-sm text-gray-800 dark:text-zinc-200 truncate">{prev?.title || '—'}</div>
                </div>
              </button>
              <button
                onClick={() => next && openPage(next.id)}
                disabled={!next}
                className="flex-1 flex items-center gap-2 bg-white dark:bg-zinc-900 border border-gray-200 dark:border-zinc-700 rounded-xl px-4 py-3 text-right hover:bg-gray-50 dark:hover:bg-zinc-800 disabled:opacity-40 disabled:hover:bg-transparent transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-gray-400 dark:text-zinc-500">下一頁</div>
                  <div className="text-sm text-gray-800 dark:text-zinc-200 truncate">{next?.title || '—'}</div>
                </div>
                <ChevronRight size={18} className="text-gray-400 dark:text-zinc-500 flex-shrink-0" />
              </button>
            </nav>
          )
        })()}
      </div>
    )
  }

  return (
    <div className="p-8 max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-800 dark:text-zinc-100">Wiki 頁面</h1>
          <p className="text-sm text-gray-500 dark:text-zinc-400 mt-1">{pages.length} 個頁面</p>
        </div>
        <button
          onClick={handleLint}
          disabled={linting}
          className="flex items-center gap-2 bg-amber-50 dark:bg-amber-950 border border-amber-300 dark:border-amber-700 text-amber-700 dark:text-amber-400 px-3 py-2 rounded-lg text-sm hover:bg-amber-100 dark:hover:bg-amber-900 disabled:opacity-50"
        >
          {linting ? <RefreshCw size={14} className="animate-spin" /> : <AlertTriangle size={14} />}
          健檢 Wiki
        </button>
      </div>

      {error && (
        <p className="mb-4 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 rounded-lg px-3 py-2">{error}</p>
      )}

      {applyResult && (
        <div className="mb-4 text-sm text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 rounded-lg px-3 py-2">
          已套用 {applyResult.applied} 頁{applyResult.skipped > 0 ? `，略過 ${applyResult.skipped}` : ''}。可重新執行健檢驗證。
        </div>
      )}

      {lintReport && (
        <div className="mb-6 bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 rounded-xl p-4">
          <div className="flex items-start justify-between gap-3 mb-2">
            <h3 className="font-semibold text-amber-800 dark:text-amber-300">健檢報告</h3>
            {lintReport.issues?.length > 0 && (
              <button
                onClick={handleApplyAll}
                disabled={applying !== null}
                className="flex items-center gap-1.5 text-xs bg-amber-600 text-white px-2.5 py-1.5 rounded-lg hover:bg-amber-700 disabled:opacity-50"
              >
                {applying === 'all' ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
                自動修復全部
              </button>
            )}
          </div>
          <p className="text-sm text-amber-700 dark:text-amber-400 mb-3">{lintReport.summary}</p>
          <div className="flex gap-4 text-xs text-amber-600 dark:text-amber-500 mb-3">
            <span>總頁數：{lintReport.stats?.total_pages}</span>
            <span>孤立頁：{lintReport.stats?.orphan_pages}</span>
            <span>問題數：{lintReport.stats?.issues_found}</span>
          </div>
          {lintReport.issues?.length > 0 && (
            <ul className="space-y-2 max-h-96 overflow-y-auto">
              {lintReport.issues.map((issue: any, i: number) => (
                <li key={i} className="text-xs bg-white dark:bg-zinc-900 rounded p-2 border border-amber-200 dark:border-amber-800 flex items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <div>
                      <span className={`font-medium ${issue.severity === 'high' ? 'text-red-600 dark:text-red-400' : issue.severity === 'medium' ? 'text-orange-600 dark:text-orange-400' : 'text-yellow-600 dark:text-yellow-400'}`}>
                        [{issue.severity}]
                      </span>{' '}
                      <span className="text-gray-400 dark:text-zinc-500">{issue.page_slug}</span>{' '}
                      <span className="text-gray-700 dark:text-zinc-300">{issue.description}</span>
                    </div>
                    {issue.suggestion && <p className="text-gray-500 dark:text-zinc-400 mt-1">建議：{issue.suggestion}</p>}
                  </div>
                  {issue.page_slug && (
                    <button
                      onClick={() => runApply([issue as LintIssue], i)}
                      disabled={applying !== null}
                      title="套用此建議"
                      className="shrink-0 flex items-center gap-1 text-xs border border-amber-300 dark:border-amber-700 text-amber-700 dark:text-amber-400 px-2 py-1 rounded hover:bg-amber-100 dark:hover:bg-amber-900 disabled:opacity-50"
                    >
                      {applying === i ? <Loader2 size={10} className="animate-spin" /> : <Wand2 size={10} />}
                      套用
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <RefreshCw size={24} className="animate-spin text-gray-400 dark:text-zinc-500" />
        </div>
      ) : pages.length === 0 ? (
        <div className="text-center py-16 text-gray-400 dark:text-zinc-500">
          <BookOpen size={40} className="mx-auto mb-3 opacity-30" />
          <p>尚未有 wiki 頁面，請先上傳文件</p>
        </div>
      ) : (
        <div className="grid gap-3">
          {pages.map((page) => (
            <button
              key={page.id}
              onClick={() => openPage(page.id)}
              className="bg-white dark:bg-zinc-900 border border-gray-200 dark:border-zinc-700 rounded-xl p-4 text-left hover:border-blue-300 dark:hover:border-blue-600 hover:shadow-sm transition-all"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${PAGE_TYPE_COLOR[page.page_type] || 'bg-gray-100 text-gray-600 dark:bg-zinc-800 dark:text-zinc-400'}`}>
                      {page.page_type}
                    </span>
                  </div>
                  <h3 className="font-medium text-gray-800 dark:text-zinc-200 truncate">{page.title}</h3>
                </div>
                <span className="text-xs text-gray-400 dark:text-zinc-500 flex-shrink-0">
                  {new Date(page.updated_at).toLocaleDateString('zh-TW')}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
