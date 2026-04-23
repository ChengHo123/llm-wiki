import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { BookOpen, ChevronLeft, AlertTriangle, RefreshCw, Trash2 } from 'lucide-react'
import { listWikiPages, getWikiPage, lintWiki, deleteWikiPage, type WikiPageSummary, type WikiPageDetail } from '../api/client'

const PAGE_TYPE_COLOR: Record<string, string> = {
  index: 'bg-purple-100 text-purple-700',
  summary: 'bg-blue-100 text-blue-700',
  entity: 'bg-green-100 text-green-700',
  concept: 'bg-orange-100 text-orange-700',
}

export default function WikiPage() {
  const [pages, setPages] = useState<WikiPageSummary[]>([])
  const [selected, setSelected] = useState<WikiPageDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [lintReport, setLintReport] = useState<any>(null)
  const [linting, setLinting] = useState(false)
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
    try {
      setLintReport(await lintWiki())
    } catch {
      setError('Lint 執行失敗')
    } finally {
      setLinting(false)
    }
  }

  if (selected) {
    return (
      <div className="p-8 max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <button
            onClick={() => setSelected(null)}
            className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700"
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
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${PAGE_TYPE_COLOR[selected.page_type] || 'bg-gray-100 text-gray-600'}`}>
            {selected.page_type}
          </span>
        </div>
        <h1 className="text-2xl font-bold text-gray-800 mb-4">{selected.title}</h1>
        <div className="bg-white rounded-xl border border-gray-200 p-6 prose max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected.content}</ReactMarkdown>
        </div>
        <p className="text-xs text-gray-400 mt-3">最後更新：{new Date(selected.updated_at).toLocaleString('zh-TW')}</p>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Wiki 頁面</h1>
          <p className="text-sm text-gray-500 mt-1">{pages.length} 個頁面</p>
        </div>
        <button
          onClick={handleLint}
          disabled={linting}
          className="flex items-center gap-2 bg-amber-50 border border-amber-300 text-amber-700 px-3 py-2 rounded-lg text-sm hover:bg-amber-100 disabled:opacity-50"
        >
          {linting ? <RefreshCw size={14} className="animate-spin" /> : <AlertTriangle size={14} />}
          健檢 Wiki
        </button>
      </div>

      {error && (
        <p className="mb-4 text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>
      )}

      {lintReport && (
        <div className="mb-6 bg-amber-50 border border-amber-200 rounded-xl p-4">
          <h3 className="font-semibold text-amber-800 mb-2">健檢報告</h3>
          <p className="text-sm text-amber-700 mb-3">{lintReport.summary}</p>
          <div className="flex gap-4 text-xs text-amber-600 mb-3">
            <span>總頁數：{lintReport.stats?.total_pages}</span>
            <span>孤立頁：{lintReport.stats?.orphan_pages}</span>
            <span>問題數：{lintReport.stats?.issues_found}</span>
          </div>
          {lintReport.issues?.length > 0 && (
            <ul className="space-y-2">
              {lintReport.issues.slice(0, 5).map((issue: any, i: number) => (
                <li key={i} className="text-xs bg-white rounded p-2 border border-amber-200">
                  <span className={`font-medium ${issue.severity === 'high' ? 'text-red-600' : issue.severity === 'medium' ? 'text-orange-600' : 'text-yellow-600'}`}>
                    [{issue.severity}]
                  </span>{' '}
                  <span className="text-gray-700">{issue.description}</span>
                  {issue.suggestion && <p className="text-gray-500 mt-1">建議：{issue.suggestion}</p>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <RefreshCw size={24} className="animate-spin text-gray-400" />
        </div>
      ) : pages.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <BookOpen size={40} className="mx-auto mb-3 opacity-30" />
          <p>尚未有 wiki 頁面，請先上傳文件</p>
        </div>
      ) : (
        <div className="grid gap-3">
          {pages.map((page) => (
            <button
              key={page.id}
              onClick={() => openPage(page.id)}
              className="bg-white border border-gray-200 rounded-xl p-4 text-left hover:border-blue-300 hover:shadow-sm transition-all"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${PAGE_TYPE_COLOR[page.page_type] || 'bg-gray-100 text-gray-600'}`}>
                      {page.page_type}
                    </span>
                  </div>
                  <h3 className="font-medium text-gray-800 truncate">{page.title}</h3>
                </div>
                <span className="text-xs text-gray-400 flex-shrink-0">
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
