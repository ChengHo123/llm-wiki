import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronLeft, RefreshCw, Pause, Play } from 'lucide-react'
import { adminLogs, type LogEntry } from '../api/client'

type LevelFilter = 'ALL' | 'INFO' | 'WARNING' | 'ERROR'

const LEVEL_ORDER: Record<string, number> = {
  DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4,
}

const LEVEL_STYLE: Record<string, string> = {
  DEBUG:    'bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400',
  INFO:     'bg-blue-50  dark:bg-blue-950/40 text-blue-700 dark:text-blue-300',
  WARNING:  'bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300',
  ERROR:    'bg-red-50   dark:bg-red-950/40 text-red-700 dark:text-red-300',
  CRITICAL: 'bg-red-100  dark:bg-red-950/60 text-red-800 dark:text-red-200 font-semibold',
}

const ROW_STYLE: Record<string, string> = {
  DEBUG:    '',
  INFO:     '',
  WARNING:  'bg-amber-50/40 dark:bg-amber-950/10',
  ERROR:    'bg-red-50/40   dark:bg-red-950/10',
  CRITICAL: 'bg-red-50/60   dark:bg-red-950/20',
}

function shortLogger(name: string): string {
  const parts = name.split('.')
  return parts.length > 2 ? parts.slice(-2).join('.') : name
}

function shortTime(iso: string): string {
  return iso.slice(11, 19)  // HH:MM:SS from YYYY-MM-DDTHH:MM:SSZ
}

export default function AdminLogsPage() {
  const navigate = useNavigate()
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [levelFilter, setLevelFilter] = useState<LevelFilter>('ALL')
  const [search, setSearch] = useState('')
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetch = useCallback(async () => {
    try {
      const data = await adminLogs(500)
      setLogs([...data].reverse())
      setError('')
    } catch (e: any) {
      if (e?.response?.status === 401) {
        navigate('/admin/login')
        return
      }
      setError('載入失敗')
    } finally {
      setLoading(false)
    }
  }, [navigate])

  useEffect(() => {
    fetch()
  }, [fetch])

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current)
    if (autoRefresh) {
      intervalRef.current = setInterval(fetch, 3000)
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [autoRefresh, fetch])

  const minLevel = LEVEL_ORDER[levelFilter] ?? 0

  const filtered = logs.filter((l) => {
    if (levelFilter !== 'ALL' && (LEVEL_ORDER[l.level] ?? 0) < minLevel) return false
    if (search) {
      const q = search.toLowerCase()
      if (!l.message.toLowerCase().includes(q) && !l.logger.toLowerCase().includes(q)) return false
    }
    return true
  })

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <header className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/admin/overview')}
              className="flex items-center gap-1 text-sm text-zinc-500 dark:text-zinc-400
                         hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors"
            >
              <ChevronLeft size={16} /> 返回
            </button>
            <h1 className="text-lg font-bold text-zinc-900 dark:text-zinc-100">後端 Logs</h1>
            <span className="text-xs text-zinc-400 dark:text-zinc-500">
              最新 500 筆，新的在上
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={fetch}
              className="w-8 h-8 rounded-lg inline-flex items-center justify-center
                         text-zinc-500 dark:text-zinc-400
                         hover:text-zinc-900 dark:hover:text-zinc-100
                         hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
              title="立即重新整理"
            >
              <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={() => setAutoRefresh((v) => !v)}
              className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border transition-colors ${
                autoRefresh
                  ? 'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-300 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400'
                  : 'bg-zinc-100 dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700 text-zinc-500 dark:text-zinc-400'
              }`}
            >
              {autoRefresh ? <><Pause size={12} /> 暫停</> : <><Play size={12} /> 自動更新</>}
            </button>
          </div>
        </header>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2 mb-4">
          {(['ALL', 'INFO', 'WARNING', 'ERROR'] as LevelFilter[]).map((lv) => (
            <button
              key={lv}
              onClick={() => setLevelFilter(lv)}
              className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
                levelFilter === lv
                  ? 'bg-blue-500 text-white border-blue-500'
                  : 'bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-300 hover:border-blue-300 dark:hover:border-blue-700'
              }`}
            >
              {lv}
            </button>
          ))}

          <input
            type="text"
            placeholder="搜尋訊息 / logger…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="ml-2 flex-1 min-w-48 max-w-xs text-xs
                       bg-white dark:bg-zinc-900
                       border border-zinc-200 dark:border-zinc-700
                       rounded-lg px-3 py-1.5
                       text-zinc-700 dark:text-zinc-200
                       placeholder:text-zinc-400 dark:placeholder:text-zinc-600
                       focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          />

          <span className="text-xs text-zinc-400 dark:text-zinc-500 ml-auto">
            顯示 {filtered.length} / {logs.length} 筆
          </span>
        </div>

        {error && (
          <p className="mb-4 text-sm text-red-600 dark:text-red-400
                        bg-red-50 dark:bg-red-950/30 border border-red-200/50 dark:border-red-900/50
                        rounded-lg px-3 py-2">{error}</p>
        )}

        {/* Log table */}
        <div className="bg-white dark:bg-zinc-900
                        border border-zinc-200 dark:border-zinc-800
                        rounded-2xl overflow-hidden
                        shadow-sm shadow-zinc-200/40 dark:shadow-black/20">
          {filtered.length === 0 ? (
            <p className="text-sm text-zinc-400 dark:text-zinc-500 text-center py-12">
              {loading ? '載入中…' : '無符合條件的 log'}
            </p>
          ) : (
            <div className="divide-y divide-zinc-100 dark:divide-zinc-800 font-mono text-xs">
              {filtered.map((l, i) => (
                <div
                  key={i}
                  className={`flex items-start gap-3 px-4 py-2 ${ROW_STYLE[l.level] ?? ''}`}
                >
                  <span className="shrink-0 text-zinc-400 dark:text-zinc-600 w-16 tabular-nums">
                    {shortTime(l.time)}
                  </span>
                  <span className={`shrink-0 inline-block px-1.5 py-0.5 rounded text-[10px] font-medium w-16 text-center ${LEVEL_STYLE[l.level] ?? LEVEL_STYLE.INFO}`}>
                    {l.level}
                  </span>
                  <span className="shrink-0 w-36 text-zinc-400 dark:text-zinc-600 truncate"
                        title={l.logger}>
                    {shortLogger(l.logger)}
                  </span>
                  <span className="flex-1 break-all text-zinc-800 dark:text-zinc-200 whitespace-pre-wrap leading-relaxed">
                    {l.message}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
