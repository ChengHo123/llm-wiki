import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Users, LogOut, RefreshCw, MessageCircle, FileText, Activity, BarChart3, UserCheck, BookOpen } from 'lucide-react'
import { adminListUsers, adminLogout, adminBackfillLineNames, adminBackfillWikiSummaries, type AdminUserSummary } from '../api/client'
import ThemeToggle from '../components/ThemeToggle'

export default function AdminUsers() {
  const navigate = useNavigate()
  const [users, setUsers] = useState<AdminUserSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      setUsers(await adminListUsers())
    } catch (e: any) {
      if (e.response?.status === 401) {
        navigate('/admin/login')
        return
      }
      setError(e.response?.data?.detail || '載入失敗')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  // 有人在跑任務時自動每 5 秒 refresh
  useEffect(() => {
    if (!users.some((u) => u.in_progress_count > 0)) return
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [users])

  const handleLogout = async () => {
    await adminLogout()
    navigate('/admin/login')
  }

  const [backfilling, setBackfilling] = useState(false)
  const handleBackfill = async () => {
    if (backfilling) return
    setBackfilling(true)
    try {
      const r = await adminBackfillLineNames()
      alert(`掃描 ${r.scanned}，更新 ${r.updated}，失敗 ${r.failed}`)
      await load()
    } catch (e: any) {
      alert(e.response?.data?.detail || '補抓失敗')
    } finally {
      setBackfilling(false)
    }
  }

  const [backfillingSummaries, setBackfillingSummaries] = useState(false)
  const handleBackfillSummaries = async () => {
    if (backfillingSummaries) return
    if (!confirm('補抓 wiki summary 會逐頁呼叫 LLM，可能花費較久時間且耗 token，是否繼續？')) return
    setBackfillingSummaries(true)
    try {
      const r = await adminBackfillWikiSummaries()
      alert(`掃描 ${r.scanned} 頁，更新 ${r.updated}，失敗 ${r.failed}`)
    } catch (e: any) {
      alert(e.response?.data?.detail || '補抓 summary 失敗')
    } finally {
      setBackfillingSummaries(false)
    }
  }

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 p-6">
      <div className="max-w-6xl mx-auto">
        <header className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-2.5">
            <div className="rounded-xl bg-blue-50 dark:bg-blue-950/50 p-2">
              <Users size={18} className="text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-zinc-900 dark:text-zinc-100 leading-tight">
                使用者列表
              </h1>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">管理後台</p>
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            <Link
              to="/admin/overview"
              className="text-sm text-zinc-600 dark:text-zinc-300
                         hover:text-blue-600 dark:hover:text-blue-400
                         flex items-center gap-1 px-2.5 py-1.5 rounded-lg
                         hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            >
              <BarChart3 size={14} />
              平台總覽
            </Link>
            <button
              onClick={handleBackfill}
              disabled={backfilling}
              className="text-zinc-500 dark:text-zinc-400
                         hover:text-zinc-900 dark:hover:text-zinc-100
                         hover:bg-zinc-100 dark:hover:bg-zinc-800
                         disabled:opacity-50 disabled:cursor-not-allowed
                         w-8 h-8 rounded-lg inline-flex items-center justify-center transition-colors"
              title="補抓 LINE 顯示名稱"
            >
              <UserCheck size={15} className={backfilling ? 'animate-pulse' : ''} />
            </button>
            <button
              onClick={handleBackfillSummaries}
              disabled={backfillingSummaries}
              className="text-zinc-500 dark:text-zinc-400
                         hover:text-zinc-900 dark:hover:text-zinc-100
                         hover:bg-zinc-100 dark:hover:bg-zinc-800
                         disabled:opacity-50 disabled:cursor-not-allowed
                         w-8 h-8 rounded-lg inline-flex items-center justify-center transition-colors"
              title="補抓 wiki page summary（一次性）"
            >
              <BookOpen size={15} className={backfillingSummaries ? 'animate-pulse' : ''} />
            </button>
            <button
              onClick={load}
              className="text-zinc-500 dark:text-zinc-400
                         hover:text-zinc-900 dark:hover:text-zinc-100
                         hover:bg-zinc-100 dark:hover:bg-zinc-800
                         w-8 h-8 rounded-lg inline-flex items-center justify-center transition-colors"
              title="重新整理"
            >
              <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
            </button>
            <ThemeToggle />
            <button
              onClick={handleLogout}
              className="text-sm text-zinc-500 dark:text-zinc-400
                         hover:text-red-600 dark:hover:text-red-400
                         flex items-center gap-1 px-2.5 py-1.5 rounded-lg
                         hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors"
            >
              <LogOut size={14} />
              登出
            </button>
          </div>
        </header>

        {error && (
          <p className="mb-4 text-sm text-red-600 dark:text-red-400
                        bg-red-50 dark:bg-red-950/30
                        border border-red-200/50 dark:border-red-900/50
                        rounded-lg px-3 py-2">{error}</p>
        )}

        <div className="bg-white dark:bg-zinc-900
                        rounded-2xl border border-zinc-200 dark:border-zinc-800
                        overflow-hidden shadow-sm shadow-zinc-200/40 dark:shadow-black/20">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 dark:bg-zinc-900/50
                                text-zinc-500 dark:text-zinc-400
                                text-xs uppercase tracking-wider
                                border-b border-zinc-200 dark:border-zinc-800">
                <tr>
                  <th className="px-4 py-3 text-left font-medium">名稱 / LINE</th>
                  <th className="px-4 py-3 text-right font-medium">文件</th>
                  <th className="px-4 py-3 text-right font-medium">Wiki 頁</th>
                  <th className="px-4 py-3 text-right font-medium">聊天</th>
                  <th className="px-4 py-3 text-right font-medium">進行中</th>
                  <th className="px-4 py-3 text-left font-medium">建立時間</th>
                  <th className="px-4 py-3 text-left font-medium">LiteLLM tag</th>
                </tr>
              </thead>
              <tbody>
                {loading && users.length === 0 ? (
                  <tr><td colSpan={7} className="text-center text-zinc-400 dark:text-zinc-500 py-8">載入中…</td></tr>
                ) : users.length === 0 ? (
                  <tr><td colSpan={7} className="text-center text-zinc-400 dark:text-zinc-500 py-8">尚無使用者</td></tr>
                ) : (
                  users.map((u) => (
                    <tr key={u.api_key_id}
                        className="border-t border-zinc-100 dark:border-zinc-800
                                   hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors">
                      <td className="px-4 py-3">
                        <Link
                          to={`/admin/users/${u.api_key_id}`}
                          className="font-medium text-blue-600 dark:text-blue-400 hover:underline"
                        >
                          {u.name}
                        </Link>
                        {u.line_user_id && (
                          <div className="text-xs text-zinc-400 dark:text-zinc-500 font-mono mt-0.5">
                            line: {u.line_user_id.slice(0, 12)}…
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        <span className="inline-flex items-center gap-1 text-zinc-700 dark:text-zinc-200">
                          <FileText size={12} className="text-zinc-400" />
                          {u.document_count}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right text-zinc-700 dark:text-zinc-200 tabular-nums">
                        {u.wiki_page_count}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        <span className="inline-flex items-center gap-1 text-zinc-700 dark:text-zinc-200">
                          <MessageCircle size={12} className="text-zinc-400" />
                          {u.chat_count}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {u.in_progress_count > 0 ? (
                          <span className="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400 font-medium">
                            <Activity size={12} className="animate-pulse" />
                            {u.in_progress_count}
                          </span>
                        ) : (
                          <span className="text-zinc-300 dark:text-zinc-600">0</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-zinc-500 dark:text-zinc-400 text-xs">
                        {new Date(u.created_at).toLocaleString('zh-TW')}
                      </td>
                      <td className="px-4 py-3 text-xs font-mono text-zinc-400 dark:text-zinc-500">
                        {u.end_user_tag.slice(0, 20)}…
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <p className="mt-4 text-xs text-zinc-400 dark:text-zinc-500">
          Token / spend 統計請到{' '}
          <a href="http://localhost:4000" target="_blank"
             className="underline hover:text-zinc-600 dark:hover:text-zinc-300">
            LiteLLM UI
          </a>
          {' '}→ End-Users 頁，比對上面的 LiteLLM tag。
        </p>
      </div>
    </div>
  )
}
