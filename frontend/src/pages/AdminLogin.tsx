import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Lock, Sparkles } from 'lucide-react'
import { adminLogin } from '../api/client'
import ThemeToggle from '../components/ThemeToggle'

export default function AdminLogin() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError('')
    try {
      await adminLogin(username.trim(), password)
      navigate(searchParams.get('next') || '/admin/overview')
    } catch (e: any) {
      setError(e.response?.data?.detail || '登入失敗')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen relative bg-gradient-to-br from-zinc-50 via-zinc-100 to-zinc-50
                    dark:from-zinc-950 dark:via-zinc-900 dark:to-zinc-950
                    flex items-center justify-center p-6">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>

      <form
        onSubmit={handleSubmit}
        className="relative bg-white dark:bg-zinc-900 rounded-2xl
                   border border-zinc-200 dark:border-zinc-800
                   p-8 w-full max-w-sm space-y-5 shadow-lg shadow-zinc-200/40 dark:shadow-black/20"
      >
        <div className="flex items-center gap-2.5 pb-1">
          <div className="rounded-xl bg-blue-50 dark:bg-blue-950/50 p-2">
            <Lock size={18} className="text-blue-600 dark:text-blue-400" />
          </div>
          <div>
            <h1 className="font-bold text-zinc-900 dark:text-zinc-100">管理員登入</h1>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 inline-flex items-center gap-1">
              <Sparkles size={10} /> LLM Wiki 後台
            </p>
          </div>
        </div>

        <div className="space-y-3">
          <Field label="帳號">
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-white dark:bg-zinc-950
                         border border-zinc-300 dark:border-zinc-700
                         rounded-lg px-3 py-2 text-sm
                         text-zinc-900 dark:text-zinc-100
                         placeholder:text-zinc-400 dark:placeholder:text-zinc-600
                         focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
              autoFocus
            />
          </Field>

          <Field label="密碼">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-white dark:bg-zinc-950
                         border border-zinc-300 dark:border-zinc-700
                         rounded-lg px-3 py-2 text-sm
                         text-zinc-900 dark:text-zinc-100
                         placeholder:text-zinc-400 dark:placeholder:text-zinc-600
                         focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
            />
          </Field>
        </div>

        {error && (
          <p className="text-sm text-red-600 dark:text-red-400
                        bg-red-50 dark:bg-red-950/30
                        border border-red-200/50 dark:border-red-900/50
                        rounded-lg px-3 py-2">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting || !username || !password}
          className="w-full bg-blue-600 hover:bg-blue-700 active:bg-blue-800
                     dark:bg-blue-500 dark:hover:bg-blue-400
                     text-white font-medium py-2.5 rounded-lg text-sm
                     disabled:opacity-50 disabled:cursor-not-allowed
                     transition-colors shadow-sm shadow-blue-600/20"
        >
          {submitting ? '登入中…' : '登入'}
        </button>
      </form>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400 mb-1 block">
        {label}
      </span>
      {children}
    </label>
  )
}
