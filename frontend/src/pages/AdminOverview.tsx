import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  BarChart3, Users, FileText, BookOpen, MessageCircle,
  Activity, RefreshCw, LogOut, ListOrdered, AlertTriangle,
  TrendingUp, TrendingDown, Minus, Coins, Cpu, CalendarRange, BrainCircuit, ScrollText,
} from 'lucide-react'
import {
  adminOverview, adminSpend, adminLogout,
  type AdminOverview, type AdminLeaderEntry, type AdminTrendPoint,
  type AdminSpend, type AdminSpendUser,
} from '../api/client'
import ThemeToggle from '../components/ThemeToggle'

type LeaderTab = 'uploaders' | 'queriers' | 'wiki'
type Preset = 1 | 7 | 14 | 30 | 90 | 'custom'

function todayUTC(): Date {
  const d = new Date()
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
}

function toIsoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

function presetRange(days: number): { start: string; end: string } {
  const end = todayUTC()
  const start = new Date(end)
  start.setUTCDate(end.getUTCDate() - (days - 1))
  return { start: toIsoDate(start), end: toIsoDate(end) }
}

export default function AdminOverviewPage() {
  const navigate = useNavigate()
  const [data, setData] = useState<AdminOverview | null>(null)
  const [spend, setSpend] = useState<AdminSpend | null>(null)
  const [spendError, setSpendError] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<LeaderTab>('uploaders')

  const [preset, setPreset] = useState<Preset>(7)
  const [{ start, end }, setDates] = useState<{ start: string; end: string }>(presetRange(7))

  const applyPreset = (p: Preset) => {
    setPreset(p)
    if (p !== 'custom') {
      setDates(presetRange(p))
    }
  }

  const load = async (s = start, e = end) => {
    setLoading(true)
    setError('')
    setSpendError('')
    try {
      const [ov, sp] = await Promise.allSettled([adminOverview(s, e), adminSpend(s, e)])
      if (ov.status === 'fulfilled') setData(ov.value)
      else {
        if (ov.reason?.response?.status === 401) {
          navigate('/admin/login')
          return
        }
        setError(ov.reason?.response?.data?.detail || '載入失敗')
      }
      if (sp.status === 'fulfilled') setSpend(sp.value)
      else setSpendError(sp.reason?.response?.data?.detail || 'Token 統計載入失敗')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(start, end) }, [start, end])

  useEffect(() => {
    if (!data || data.kpi.queue_depth === 0) return
    const t = setInterval(() => load(start, end), 5000)
    return () => clearInterval(t)
  }, [data, start, end])

  const handleLogout = async () => {
    await adminLogout()
    navigate('/admin/login')
  }

  if (!data && loading) {
    return (
      <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 p-6 text-zinc-400 dark:text-zinc-500">
        載入中…
      </div>
    )
  }
  if (!data) {
    return (
      <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 p-6">
        <p className="text-sm text-red-600 dark:text-red-400
                      bg-red-50 dark:bg-red-950/30
                      border border-red-200/50 dark:border-red-900/50
                      rounded-lg px-3 py-2">{error || '無資料'}</p>
      </div>
    )
  }

  const k = data.kpi
  const leaderData: Record<LeaderTab, { label: string; entries: AdminLeaderEntry[]; unit: string }> = {
    uploaders: { label: '文件上傳排行', entries: data.top_uploaders, unit: '份' },
    queriers:  { label: '查詢次數排行', entries: data.top_queriers,  unit: '次' },
    wiki:      { label: 'Wiki 頁數排行', entries: data.top_wiki,      unit: '頁' },
  }
  const current = leaderData[tab]
  const maxValue = Math.max(...current.entries.map((e) => e.value), 1)
  const userDelta = k.new_users_this_week - k.new_users_last_week

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <header className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-2.5">
            <div className="rounded-xl bg-blue-50 dark:bg-blue-950/50 p-2">
              <BarChart3 size={18} className="text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-zinc-900 dark:text-zinc-100 leading-tight">
                平台總覽
              </h1>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">管理後台</p>
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            <Link
              to="/admin/users"
              className="text-sm text-zinc-600 dark:text-zinc-300
                         hover:text-blue-600 dark:hover:text-blue-400
                         flex items-center gap-1 px-2.5 py-1.5 rounded-lg
                         hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            >
              <Users size={14} />
              使用者列表
            </Link>
            <Link
              to="/admin/logs"
              className="text-sm text-zinc-600 dark:text-zinc-300
                         hover:text-blue-600 dark:hover:text-blue-400
                         flex items-center gap-1 px-2.5 py-1.5 rounded-lg
                         hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            >
              <ScrollText size={14} />
              Logs
            </Link>
            <a
              href={`${window.location.protocol}//${window.location.hostname}:4000/ui`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-zinc-600 dark:text-zinc-300
                         hover:text-blue-600 dark:hover:text-blue-400
                         flex items-center gap-1 px-2.5 py-1.5 rounded-lg
                         hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            >
              <BrainCircuit size={14} />
              LiteLLM
            </a>
            <button
              onClick={() => load(start, end)}
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

        <RangeSelector
          preset={preset}
          start={start}
          end={end}
          days={data.range.days}
          onPreset={applyPreset}
          onCustom={(s, e) => {
            setPreset('custom')
            setDates({ start: s, end: e })
          }}
        />

        {error && (
          <p className="mb-4 text-sm text-red-600 dark:text-red-400
                        bg-red-50 dark:bg-red-950/30
                        border border-red-200/50 dark:border-red-900/50
                        rounded-lg px-3 py-2">{error}</p>
        )}

        {k.queue_depth > 0 && (
          <Alert kind="info">
            <Activity size={14} className="animate-pulse" />
            目前有 <strong className="font-semibold">{k.queue_depth}</strong> 份文件在排隊或處理中
          </Alert>
        )}

        {k.range_success_rate !== null && k.range_success_rate < 0.9 && k.range_ingest_total >= 3 && (
          <Alert kind="error">
            <AlertTriangle size={14} />
            範圍內 ingest 成功率 <strong className="font-semibold">{(k.range_success_rate * 100).toFixed(0)}%</strong>，
            低於 90%（共 {k.range_ingest_total} 份，失敗 {k.range_ingest_error}）
          </Alert>
        )}

        {/* KPI cards */}
        <section className="mb-6">
          <h2 className="text-xs uppercase tracking-wider text-zinc-400 dark:text-zinc-500 mb-2">
            平台健康度
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Kpi
              icon={<Users size={14} className="text-blue-500" />}
              label="總用戶"
              value={k.total_users}
              sub={
                <TrendBadge delta={userDelta}>
                  本週新增 {k.new_users_this_week}
                </TrendBadge>
              }
            />
            <Kpi
              icon={<Activity size={14} className="text-emerald-500" />}
              label="DAU / WAU / MAU"
              value={`${k.dau} / ${k.wau} / ${k.mau}`}
              sub={<span className="text-zinc-400 dark:text-zinc-500">過去 1 / 7 / 30 天活躍</span>}
            />
            <Kpi
              icon={<FileText size={14} className="text-purple-500" />}
              label="文件總數"
              value={k.total_documents}
              sub={<span className="text-zinc-400 dark:text-zinc-500">Wiki 頁 {k.total_wiki_pages}</span>}
            />
            <Kpi
              icon={
                <span className={
                  k.range_success_rate === null ? 'text-zinc-300 dark:text-zinc-600'
                    : k.range_success_rate >= 0.9 ? 'text-emerald-500'
                    : k.range_success_rate >= 0.7 ? 'text-amber-500'
                    : 'text-red-500'
                }>
                  <BookOpen size={14} />
                </span>
              }
              label="範圍內 ingest 成功率"
              value={
                k.range_success_rate === null
                  ? '—'
                  : `${(k.range_success_rate * 100).toFixed(0)}%`
              }
              sub={
                <span className="text-zinc-400 dark:text-zinc-500">
                  完成 {k.range_ingest_done} / 失敗 {k.range_ingest_error} / 總 {k.range_ingest_total}
                </span>
              }
            />
          </div>
        </section>

        {/* Trends */}
        <section className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          <Card>
            <CardHeader
              icon={<FileText size={14} className="text-purple-500" />}
              title={`文件處理量（${data.range.granularity === 'hour' ? '逐小時' : `${data.range.days} 天`}）`}
              right={
                <Legend items={[
                  { label: '完成', color: '#10b981' },
                  { label: '失敗', color: '#ef4444' },
                ]} />
              }
            />
            <IngestBarChart trends={data.trends} granularity={data.range.granularity ?? 'day'} />
          </Card>

          <Card>
            <CardHeader
              icon={<MessageCircle size={14} className="text-blue-500" />}
              title={`查詢次數（${data.range.granularity === 'hour' ? '逐小時' : `${data.range.days} 天`}）`}
              right={
                <span className="text-xs text-zinc-400 dark:text-zinc-500">
                  共 {data.trends.reduce((s, p) => s + p.query_count, 0)} 次
                </span>
              }
            />
            <QueryLineChart trends={data.trends} granularity={data.range.granularity ?? 'day'} />
          </Card>
        </section>

        {/* Token / Spend */}
        <Card className="mb-6">
          <CardHeader
            icon={<Coins size={14} className="text-amber-500" />}
            title={`Token 消耗（${data.range.days} 天，來自 LiteLLM）`}
            right={spend && (
              <span className="text-xs text-zinc-400 dark:text-zinc-500">
                {spend.fetched_count} 筆紀錄
                {spend.total_spend_usd > 0 && ` · 總成本 $${spend.total_spend_usd.toFixed(4)}`}
              </span>
            )}
          />

          {spendError ? (
            <p className="text-sm text-red-600 dark:text-red-400
                          bg-red-50 dark:bg-red-950/30 rounded px-3 py-2">{spendError}</p>
          ) : !spend ? (
            <p className="text-sm text-zinc-400 dark:text-zinc-500">載入中…</p>
          ) : spend.total_call_count === 0 ? (
            <p className="text-sm text-zinc-400 dark:text-zinc-500">尚無 LLM 呼叫紀錄</p>
          ) : (
            <>
              {spend.note && (
                <p className="mb-3 text-xs text-amber-700 dark:text-amber-300
                              bg-amber-50 dark:bg-amber-950/30
                              border border-amber-200/50 dark:border-amber-900/50
                              rounded-lg px-2.5 py-1.5">
                  ⓘ {spend.note}
                </p>
              )}

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
                <MiniStat label="總呼叫" value={spend.total_call_count.toLocaleString()} />
                <MiniStat
                  label="總 Tokens"
                  value={spend.total_tokens.toLocaleString()}
                  sub={`in ${(spend.total_prompt_tokens / 1000).toFixed(1)}k / out ${(spend.total_completion_tokens / 1000).toFixed(1)}k`}
                />
                <MiniStat
                  label="平均 / 次"
                  value={Math.round(spend.total_tokens / Math.max(spend.total_call_count, 1)).toLocaleString()}
                  sub="tokens"
                />
                <MiniStat
                  label="平台總成本"
                  value={spend.total_spend_usd > 0 ? `$${spend.total_spend_usd.toFixed(4)}` : '—'}
                  sub={spend.total_spend_usd === 0 ? 'NVIDIA 免費額度' : 'USD'}
                />
              </div>

              <h3 className="text-xs uppercase tracking-wider text-zinc-400 dark:text-zinc-500 mb-2">
                每用戶 Token 消耗（Top 10）
              </h3>
              <SpendUserTable users={spend.by_user.slice(0, 10)} />

              {spend.by_model.length > 1 && (
                <>
                  <h3 className="text-xs uppercase tracking-wider text-zinc-400 dark:text-zinc-500 mt-5 mb-2 inline-flex items-center gap-1">
                    <Cpu size={11} />
                    按模型分佈
                  </h3>
                  <ul className="space-y-1.5 text-sm">
                    {spend.by_model.map((m) => {
                      const maxTok = Math.max(...spend.by_model.map((x) => x.total_tokens), 1)
                      const pct = (m.total_tokens / maxTok) * 100
                      return (
                        <li key={m.model} className="flex items-center gap-3">
                          <span className="w-48 text-zinc-700 dark:text-zinc-200 truncate font-mono text-xs"
                                title={m.model}>
                            {m.model}
                          </span>
                          <div className="flex-1 bg-zinc-100 dark:bg-zinc-800 rounded-full h-4 overflow-hidden">
                            <div
                              className="h-full bg-gradient-to-r from-amber-300 to-amber-400 rounded-full"
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                          <span className="w-20 text-right text-xs tabular-nums text-zinc-700 dark:text-zinc-200">
                            {m.total_tokens.toLocaleString()}
                          </span>
                          <span className="w-12 text-right text-xs text-zinc-400 dark:text-zinc-500">
                            {m.call_count} 次
                          </span>
                        </li>
                      )
                    })}
                  </ul>
                </>
              )}
            </>
          )}
        </Card>

        {/* Leaderboard */}
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <ListOrdered size={16} className="text-zinc-500 dark:text-zinc-400" />
            <h2 className="font-semibold text-zinc-900 dark:text-zinc-100">
              用戶排行榜（{data.range.days} 天，Top 10）
            </h2>
          </div>

          <div className="flex gap-1 mb-4 border-b border-zinc-100 dark:border-zinc-800">
            <TabButton active={tab === 'uploaders'} onClick={() => setTab('uploaders')} icon={<FileText size={13} />}>
              上傳量
            </TabButton>
            <TabButton active={tab === 'queriers'} onClick={() => setTab('queriers')} icon={<MessageCircle size={13} />}>
              查詢次數
            </TabButton>
            <TabButton active={tab === 'wiki'} onClick={() => setTab('wiki')} icon={<BookOpen size={13} />}>
              Wiki 頁數
            </TabButton>
          </div>

          {current.entries.length === 0 ? (
            <p className="text-sm text-zinc-400 dark:text-zinc-500 py-6 text-center">尚無資料</p>
          ) : (
            <ul className="space-y-2">
              {current.entries.map((e, idx) => {
                const pct = (e.value / maxValue) * 100
                return (
                  <li key={e.api_key_id} className="flex items-center gap-3">
                    <span className="w-6 text-right text-xs text-zinc-400 dark:text-zinc-500 font-mono">
                      {idx + 1}
                    </span>
                    <Link
                      to={`/admin/users/${e.api_key_id}`}
                      className="w-32 text-sm text-blue-600 dark:text-blue-400 hover:underline truncate flex-shrink-0"
                      title={e.name}
                    >
                      {e.name}
                    </Link>
                    <div className="flex-1 bg-zinc-100 dark:bg-zinc-800 rounded-full h-5 overflow-hidden">
                      <div
                        className="h-full bg-gradient-to-r from-blue-400 to-blue-500 dark:from-blue-500 dark:to-blue-400 rounded-full transition-all"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="w-20 text-right text-sm text-zinc-700 dark:text-zinc-200 font-medium tabular-nums">
                      {e.value.toLocaleString()}
                      <span className="text-zinc-400 dark:text-zinc-500 text-xs ml-0.5">{current.unit}</span>
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
        </Card>

        <p className="mt-4 text-xs text-zinc-400 dark:text-zinc-500">
          資料時區為 UTC，時間範圍依 ingest / 查詢的建立時間過濾。
          如需更細的逐筆 spend，可到{' '}
          <a
            href={`${window.location.protocol}//${window.location.hostname}:4000/ui`}
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:text-zinc-600 dark:hover:text-zinc-300"
          >LiteLLM UI</a>。
        </p>
      </div>
    </div>
  )
}

// ── shared ────────────────────────────────────────────

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <section className={
      'bg-white dark:bg-zinc-900 rounded-2xl ' +
      'border border-zinc-200 dark:border-zinc-800 ' +
      'shadow-sm shadow-zinc-200/40 dark:shadow-black/20 p-5 ' +
      className
    }>
      {children}
    </section>
  )
}

function CardHeader({
  icon, title, right,
}: {
  icon?: React.ReactNode
  title: string
  right?: React.ReactNode
}) {
  return (
    <div className="flex items-center justify-between mb-3">
      <h2 className="font-semibold text-zinc-900 dark:text-zinc-100 text-sm inline-flex items-center gap-1.5">
        {icon}
        {title}
      </h2>
      {right}
    </div>
  )
}

function Alert({ kind, children }: { kind: 'info' | 'error'; children: React.ReactNode }) {
  const cls = kind === 'info'
    ? 'text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950/30 border-blue-200/50 dark:border-blue-900/50'
    : 'text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/30 border-red-200/50 dark:border-red-900/50'
  return (
    <div className={`mb-4 flex items-center gap-2 text-sm rounded-lg px-3 py-2 border ${cls}`}>
      {children}
    </div>
  )
}

function Kpi({
  icon, label, value, sub,
}: {
  icon: React.ReactNode
  label: string
  value: string | number
  sub?: React.ReactNode
}) {
  return (
    <div className="bg-white dark:bg-zinc-900
                    border border-zinc-200 dark:border-zinc-800
                    rounded-2xl p-4
                    shadow-sm shadow-zinc-200/40 dark:shadow-black/20">
      <div className="flex items-center gap-1 text-xs text-zinc-500 dark:text-zinc-400 mb-1">
        {icon}
        <span>{label}</span>
      </div>
      <div className="text-2xl font-bold text-zinc-900 dark:text-zinc-100 tabular-nums">{value}</div>
      {sub && <div className="text-xs mt-1">{sub}</div>}
    </div>
  )
}

function TrendBadge({ delta, children }: { delta: number; children: React.ReactNode }) {
  const Icon = delta > 0 ? TrendingUp : delta < 0 ? TrendingDown : Minus
  const color = delta > 0
    ? 'text-emerald-600 dark:text-emerald-400'
    : delta < 0
    ? 'text-red-500 dark:text-red-400'
    : 'text-zinc-400 dark:text-zinc-500'
  return (
    <span className={`inline-flex items-center gap-1 ${color}`}>
      <Icon size={11} />
      <span>{children}</span>
      {delta !== 0 && (
        <span className="text-zinc-400 dark:text-zinc-500">
          ({delta > 0 ? '+' : ''}{delta} vs 上週)
        </span>
      )}
    </span>
  )
}

function RangeSelector({
  preset, start, end, days, onPreset, onCustom,
}: {
  preset: Preset
  start: string
  end: string
  days: number
  onPreset: (p: Preset) => void
  onCustom: (start: string, end: string) => void
}) {
  const presets: { p: Preset; label: string }[] = [
    { p: 1,  label: '今日' },
    { p: 7,  label: '近 7 天' },
    { p: 14, label: '近 14 天' },
    { p: 30, label: '近 30 天' },
    { p: 90, label: '近 90 天' },
  ]
  return (
    <div className="mb-5 bg-white dark:bg-zinc-900
                    border border-zinc-200 dark:border-zinc-800
                    rounded-2xl px-4 py-3 flex flex-wrap items-center gap-3
                    shadow-sm shadow-zinc-200/40 dark:shadow-black/20">
      <div className="flex items-center gap-1.5 text-sm text-zinc-700 dark:text-zinc-200">
        <CalendarRange size={14} className="text-blue-500" />
        <span className="font-medium">時間範圍</span>
      </div>

      <div className="flex flex-wrap gap-1">
        {presets.map(({ p, label }) => (
          <PresetButton key={p} active={preset === p} onClick={() => onPreset(p)}>
            {label}
          </PresetButton>
        ))}
        <PresetButton active={preset === 'custom'} onClick={() => onPreset('custom')}>
          自訂
        </PresetButton>
      </div>

      <div className="flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400 ml-auto">
        <input
          type="date"
          value={start}
          max={end}
          onChange={(e) => onCustom(e.target.value, end)}
          className="bg-white dark:bg-zinc-950
                     border border-zinc-200 dark:border-zinc-700
                     rounded-md px-2 py-1 text-xs
                     text-zinc-700 dark:text-zinc-200
                     focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500
                     [color-scheme:light] dark:[color-scheme:dark]"
        />
        <span className="text-zinc-400 dark:text-zinc-500">→</span>
        <input
          type="date"
          value={end}
          min={start}
          onChange={(e) => onCustom(start, e.target.value)}
          className="bg-white dark:bg-zinc-950
                     border border-zinc-200 dark:border-zinc-700
                     rounded-md px-2 py-1 text-xs
                     text-zinc-700 dark:text-zinc-200
                     focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500
                     [color-scheme:light] dark:[color-scheme:dark]"
        />
        <span className="ml-2 text-zinc-400 dark:text-zinc-500">（{days} 天）</span>
      </div>
    </div>
  )
}

function PresetButton({
  active, onClick, children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={
        'px-2.5 py-1 text-xs rounded-md border transition-colors ' +
        (active
          ? 'bg-blue-500 text-white border-blue-500 shadow-sm shadow-blue-500/20'
          : 'bg-white dark:bg-zinc-900 ' +
            'text-zinc-600 dark:text-zinc-300 ' +
            'border-zinc-200 dark:border-zinc-700 ' +
            'hover:border-blue-300 dark:hover:border-blue-700 ' +
            'hover:text-blue-600 dark:hover:text-blue-400')
      }
    >
      {children}
    </button>
  )
}

function MiniStat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-zinc-50 dark:bg-zinc-800/50
                    border border-transparent dark:border-zinc-800
                    rounded-xl px-3 py-2.5">
      <div className="text-xs text-zinc-500 dark:text-zinc-400">{label}</div>
      <div className="text-lg font-semibold text-zinc-900 dark:text-zinc-100 tabular-nums">
        {value}
      </div>
      {sub && <div className="text-xs text-zinc-400 dark:text-zinc-500 mt-0.5">{sub}</div>}
    </div>
  )
}

function SpendUserTable({ users }: { users: AdminSpendUser[] }) {
  if (users.length === 0) {
    return <p className="text-sm text-zinc-400 dark:text-zinc-500">無資料</p>
  }
  const max = Math.max(...users.map((u) => u.total_tokens), 1)
  return (
    <ul className="space-y-1.5">
      {users.map((u, idx) => {
        const pct = (u.total_tokens / max) * 100
        const inPct = u.total_tokens > 0 ? (u.prompt_tokens / u.total_tokens) * 100 : 0
        return (
          <li key={`${u.end_user_tag}-${idx}`} className="flex items-center gap-3 text-sm">
            <span className="w-6 text-right text-xs text-zinc-400 dark:text-zinc-500 font-mono">
              {idx + 1}
            </span>
            {u.api_key_id ? (
              <Link
                to={`/admin/users/${u.api_key_id}`}
                className="w-32 text-blue-600 dark:text-blue-400 hover:underline truncate flex-shrink-0"
                title={u.name}
              >
                {u.name}
              </Link>
            ) : (
              <span className="w-32 text-zinc-500 dark:text-zinc-400 italic truncate flex-shrink-0"
                    title={u.end_user_tag}>
                {u.name}
              </span>
            )}
            <div
              className="flex-1 bg-zinc-100 dark:bg-zinc-800 rounded-full h-5 overflow-hidden flex"
              title={`prompt ${u.prompt_tokens.toLocaleString()} / completion ${u.completion_tokens.toLocaleString()}`}
              style={{ width: `${pct}%`, minWidth: '40px' }}
            >
              <div className="h-full bg-blue-500 dark:bg-blue-400" style={{ width: `${inPct}%` }} />
              <div className="h-full bg-blue-300 dark:bg-blue-600 flex-1" />
            </div>
            <span className="w-24 text-right text-sm tabular-nums text-zinc-700 dark:text-zinc-200">
              {u.total_tokens.toLocaleString()}
            </span>
            <span className="w-10 text-right text-xs text-zinc-400 dark:text-zinc-500">
              {u.call_count} 次
            </span>
          </li>
        )
      })}
    </ul>
  )
}

function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="flex items-center gap-3 text-xs text-zinc-500 dark:text-zinc-400">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm" style={{ background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  )
}

function shortDate(iso: string) {
  if (iso.includes('T')) {
    const [datePart, hour] = iso.split('T')
    const [, m, d] = datePart.split('-')
    return `${parseInt(m)}/${parseInt(d)} ${hour}h`
  }
  const [, m, d] = iso.split('-')
  return `${parseInt(m)}/${parseInt(d)}`
}

const CHART_LABEL = '#a1a1aa'  // zinc-400 — 在淺/深模式對比都夠

function IngestBarChart({ trends, granularity = 'day' }: { trends: AdminTrendPoint[]; granularity?: string }) {
  const W = 480, H = 160, padL = 28, padR = 8, padT = 8, padB = 22
  const innerW = W - padL - padR
  const innerH = H - padT - padB
  const max = Math.max(...trends.map((t) => t.ingest_done + t.ingest_error), 1)
  const barW = innerW / trends.length
  const yScale = (v: number) => innerH - (v / max) * innerH
  const ticks = max <= 4
    ? Array.from({ length: max + 1 }, (_, i) => i)
    : [0, Math.ceil(max / 2), max]
  const labelStep = granularity === 'hour' ? 6 : 2

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full text-zinc-200 dark:text-zinc-800"
         preserveAspectRatio="none">
      {ticks.map((v) => (
        <g key={v}>
          <line
            x1={padL} y1={padT + yScale(v)}
            x2={padL + innerW} y2={padT + yScale(v)}
            stroke="currentColor" strokeWidth={1}
          />
          <text
            x={padL - 4} y={padT + yScale(v) + 3}
            textAnchor="end" fontSize="10" fill={CHART_LABEL}
          >
            {v}
          </text>
        </g>
      ))}

      {trends.map((t, i) => {
        const x = padL + i * barW + barW * 0.15
        const w = barW * 0.7
        const errH = (t.ingest_error / max) * innerH
        const doneH = (t.ingest_done / max) * innerH
        const errY = padT + innerH - errH
        const doneY = padT + innerH - errH - doneH
        return (
          <g key={t.date}>
            {t.ingest_error > 0 && (
              <rect x={x} y={errY} width={w} height={errH} fill="#ef4444" rx={1.5}>
                <title>{`${t.date}：失敗 ${t.ingest_error}`}</title>
              </rect>
            )}
            {t.ingest_done > 0 && (
              <rect x={x} y={doneY} width={w} height={doneH} fill="#10b981" rx={1.5}>
                <title>{`${t.date}：完成 ${t.ingest_done}`}</title>
              </rect>
            )}
          </g>
        )
      })}

      {trends.map((t, i) =>
        i % labelStep === 0 ? (
          <text
            key={t.date}
            x={padL + i * barW + barW / 2}
            y={H - 6}
            textAnchor="middle" fontSize="9" fill={CHART_LABEL}
          >
            {shortDate(t.date)}
          </text>
        ) : null,
      )}
    </svg>
  )
}

function QueryLineChart({ trends, granularity = 'day' }: { trends: AdminTrendPoint[]; granularity?: string }) {
  const W = 480, H = 160, padL = 28, padR = 8, padT = 8, padB = 22
  const innerW = W - padL - padR
  const innerH = H - padT - padB
  const max = Math.max(...trends.map((t) => t.query_count), 1)
  const stepX = innerW / Math.max(trends.length - 1, 1)
  const yScale = (v: number) => innerH - (v / max) * innerH

  const points = trends.map((t, i) => ({
    x: padL + i * stepX,
    y: padT + yScale(t.query_count),
    v: t.query_count,
    date: t.date,
  }))
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
  const areaPath = `${path} L${points[points.length - 1].x},${padT + innerH} L${points[0].x},${padT + innerH} Z`

  const ticks = max <= 4
    ? Array.from({ length: max + 1 }, (_, i) => i)
    : [0, Math.ceil(max / 2), max]

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full text-zinc-200 dark:text-zinc-800"
         preserveAspectRatio="none">
      {ticks.map((v) => (
        <g key={v}>
          <line
            x1={padL} y1={padT + yScale(v)}
            x2={padL + innerW} y2={padT + yScale(v)}
            stroke="currentColor" strokeWidth={1}
          />
          <text
            x={padL - 4} y={padT + yScale(v) + 3}
            textAnchor="end" fontSize="10" fill={CHART_LABEL}
          >
            {v}
          </text>
        </g>
      ))}

      <path d={areaPath} fill="#3b82f6" fillOpacity={0.12} />
      <path d={path} fill="none" stroke="#3b82f6" strokeWidth={2} strokeLinejoin="round" />

      {points.map((p) => (
        <g key={p.date}>
          <circle cx={p.x} cy={p.y} r={3} fill="#3b82f6">
            <title>{`${p.date}：${p.v} 次`}</title>
          </circle>
        </g>
      ))}

      {trends.map((t, i) =>
        i % (granularity === 'hour' ? 6 : 2) === 0 ? (
          <text
            key={t.date}
            x={padL + i * stepX}
            y={H - 6}
            textAnchor="middle" fontSize="9" fill={CHART_LABEL}
          >
            {shortDate(t.date)}
          </text>
        ) : null,
      )}
    </svg>
  )
}

function TabButton({
  active, onClick, icon, children,
}: {
  active: boolean
  onClick: () => void
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={
        'flex items-center gap-1 px-3 py-2 text-sm border-b-2 -mb-px transition-colors ' +
        (active
          ? 'border-blue-500 text-blue-600 dark:text-blue-400 font-medium'
          : 'border-transparent text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100')
      }
    >
      {icon}
      {children}
    </button>
  )
}
