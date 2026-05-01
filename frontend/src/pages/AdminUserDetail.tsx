import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft, FileText, CheckCircle, XCircle, Clock, RefreshCw,
  RotateCcw, StopCircle,
} from 'lucide-react'
import {
  adminUserDetail, adminRetryDocument, adminStopDocument,
  type AdminUserDetail,
} from '../api/client'
import ThemeToggle from '../components/ThemeToggle'

const STATUS_ICON: Record<string, JSX.Element> = {
  done: <CheckCircle size={14} className="text-emerald-500" />,
  error: <XCircle size={14} className="text-red-500" />,
  processing: <RefreshCw size={14} className="text-blue-500 animate-spin" />,
  queued: <Clock size={14} className="text-amber-400" />,
}

const STATUS_LABEL: Record<string, string> = {
  done: '完成',
  error: '失敗',
  processing: '處理中',
  queued: '排隊中',
}

export default function AdminUserDetailPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState<AdminUserDetail | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<string>('')

  const load = useCallback(async () => {
    try {
      setData(await adminUserDetail(id))
    } catch (e: any) {
      if (e.response?.status === 401) {
        navigate('/admin/login')
        return
      }
      setError(e.response?.data?.detail || '載入失敗')
    }
  }, [id, navigate])

  useEffect(() => { load() }, [load])

  // 有任務在跑時 5 秒輪詢
  useEffect(() => {
    if (!data) return
    if (!data.documents.some((d) => d.status === 'processing' || d.status === 'queued')) return
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [data, load])

  const handleRetry = async (docId: string) => {
    setBusy(docId)
    try {
      await adminRetryDocument(docId)
      await load()
    } catch (e: any) {
      setError(e.response?.data?.detail || '重試失敗')
    } finally {
      setBusy('')
    }
  }

  const handleStop = async (docId: string) => {
    if (!confirm('停止這份文件的處理？已產生的 wiki 頁不會刪除。')) return
    setBusy(docId)
    try {
      await adminStopDocument(docId)
      await load()
    } catch (e: any) {
      setError(e.response?.data?.detail || '停止失敗')
    } finally {
      setBusy('')
    }
  }

  if (!data) {
    return (
      <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 p-6">
        <div className="max-w-5xl mx-auto">
          {error ? (
            <p className="text-sm text-red-600 dark:text-red-400
                          bg-red-50 dark:bg-red-950/30
                          border border-red-200/50 dark:border-red-900/50
                          rounded-lg px-3 py-2">{error}</p>
          ) : (
            <p className="text-zinc-400 dark:text-zinc-500">載入中…</p>
          )}
        </div>
      </div>
    )
  }

  const s = data.summary

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 p-6">
      <div className="max-w-5xl mx-auto">
        <header className="flex items-center justify-between mb-5">
          <Link to="/admin/users"
                className="inline-flex items-center gap-1 text-sm
                           text-zinc-500 dark:text-zinc-400
                           hover:text-zinc-900 dark:hover:text-zinc-100
                           px-2.5 py-1.5 rounded-lg
                           hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors">
            <ArrowLeft size={14} /> 回使用者列表
          </Link>
          <ThemeToggle />
        </header>

        <div className="mb-5">
          <h1 className="text-xl font-bold text-zinc-900 dark:text-zinc-100 mb-0.5">{s.name}</h1>
          <p className="text-xs text-zinc-400 dark:text-zinc-500 font-mono">{s.api_key_id}</p>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          <Stat label="文件" value={s.document_count} />
          <Stat label="Wiki 頁" value={s.wiki_page_count} />
          <Stat label="聊天次數" value={s.chat_count} />
          <Stat label="進行中" value={s.in_progress_count} highlight={s.in_progress_count > 0} />
        </div>

        <div className="grid md:grid-cols-2 gap-3 mb-6">
          {s.line_user_id && (
            <InfoRow label="LINE user id" value={s.line_user_id} mono />
          )}
          <InfoRow label="LiteLLM end-user tag" value={s.end_user_tag} mono />
        </div>

        {error && (
          <p className="mb-4 text-sm text-red-600 dark:text-red-400
                        bg-red-50 dark:bg-red-950/30
                        border border-red-200/50 dark:border-red-900/50
                        rounded-lg px-3 py-2">{error}</p>
        )}

        <section className="bg-white dark:bg-zinc-900
                            rounded-2xl border border-zinc-200 dark:border-zinc-800
                            shadow-sm shadow-zinc-200/40 dark:shadow-black/20
                            p-5 mb-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold text-zinc-900 dark:text-zinc-100">文件清單</h2>
            <button onClick={load}
                    className="w-8 h-8 inline-flex items-center justify-center rounded-lg
                               text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-100
                               hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors">
              <RefreshCw size={14} />
            </button>
          </div>

          {data.documents.length === 0 ? (
            <p className="text-sm text-zinc-400 dark:text-zinc-500">尚未上傳任何文件</p>
          ) : (
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {data.documents.map((doc) => (
                <li key={doc.id} className="flex items-center gap-3 py-2.5">
                  <FileText size={14} className="text-zinc-400 flex-shrink-0" />
                  <span className="flex-1 text-sm text-zinc-700 dark:text-zinc-200 truncate">
                    {doc.filename}
                  </span>
                  <div className="flex items-center gap-1 text-xs text-zinc-500 dark:text-zinc-400">
                    {STATUS_ICON[doc.status] ?? <Clock size={14} />}
                    <span>{STATUS_LABEL[doc.status] ?? doc.status}</span>
                  </div>
                  {(doc.status === 'queued' || doc.status === 'processing') && (
                    <button
                      onClick={() => handleStop(doc.id)}
                      disabled={busy === doc.id}
                      className="w-7 h-7 inline-flex items-center justify-center rounded
                                 text-zinc-300 dark:text-zinc-600
                                 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30
                                 disabled:opacity-30 transition-colors"
                      title="停止處理"
                    >
                      <StopCircle size={14} />
                    </button>
                  )}
                  {(doc.status === 'error' || doc.status === 'done') && (
                    <button
                      onClick={() => handleRetry(doc.id)}
                      disabled={busy === doc.id}
                      className="w-7 h-7 inline-flex items-center justify-center rounded
                                 text-zinc-300 dark:text-zinc-600
                                 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-950/30
                                 disabled:opacity-30 transition-colors"
                      title="重新處理"
                    >
                      <RotateCcw size={13} />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {data.documents.some((d) => d.status === 'error' && d.error_message) && (
            <div className="mt-3 text-xs text-zinc-400 dark:text-zinc-500 space-y-1">
              {data.documents
                .filter((d) => d.status === 'error' && d.error_message)
                .map((d) => (
                  <div key={d.id}>
                    <span className="font-mono">{d.filename}</span>: {d.error_message}
                  </div>
                ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function Stat({ label, value, highlight }: { label: string; value: number; highlight?: boolean }) {
  return (
    <div className={
      'rounded-2xl p-4 border shadow-sm shadow-zinc-200/40 dark:shadow-black/20 ' +
      (highlight
        ? 'border-blue-300 dark:border-blue-700/50 bg-blue-50/70 dark:bg-blue-950/40'
        : 'border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900')
    }>
      <div className="text-xs text-zinc-500 dark:text-zinc-400">{label}</div>
      <div className={
        'text-2xl font-bold tabular-nums ' +
        (highlight ? 'text-blue-700 dark:text-blue-300' : 'text-zinc-900 dark:text-zinc-100')
      }>{value}</div>
    </div>
  )
}

function InfoRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="bg-white dark:bg-zinc-900
                    border border-zinc-200 dark:border-zinc-800
                    rounded-2xl px-4 py-3 text-sm
                    shadow-sm shadow-zinc-200/40 dark:shadow-black/20">
      <div className="text-xs text-zinc-500 dark:text-zinc-400 mb-0.5">{label}</div>
      <div className={
        'text-zinc-700 dark:text-zinc-200 truncate ' +
        (mono ? 'font-mono text-xs' : '')
      }>{value}</div>
    </div>
  )
}
