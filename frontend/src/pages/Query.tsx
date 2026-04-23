import { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Send, BookmarkPlus, BookmarkX, BookOpen, Loader2, Brain, ChevronDown, ChevronRight } from 'lucide-react'
import { queryWikiStream } from '../api/client'

interface Message {
  role: 'user' | 'assistant'
  content: string
  thinking?: string
  thinkingDone?: boolean
  referenced_pages?: { id: string; title: string; slug: string }[]
  judge_save?: boolean
  judge_reason?: string
  saved_page?: { id: string; title: string; slug: string } | null
  streaming?: boolean
}

function splitThinking(raw: string): { thinking: string; answer: string; thinkingDone: boolean } {
  const start = raw.indexOf('<think>')
  if (start === -1) return { thinking: '', answer: raw, thinkingDone: true }
  const before = raw.slice(0, start)
  const end = raw.indexOf('</think>', start)
  if (end === -1) {
    return {
      thinking: raw.slice(start + 7),
      answer: before,
      thinkingDone: false,
    }
  }
  return {
    thinking: raw.slice(start + 7, end),
    answer: before + raw.slice(end + 8),
    thinkingDone: true,
  }
}

function ThinkingBlock({ text, done }: { text: string; done: boolean }) {
  const [open, setOpen] = useState(!done)
  useEffect(() => {
    if (done) setOpen(false)
  }, [done])

  return (
    <div className="mb-3 border border-purple-200 rounded-lg bg-purple-50/50 overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs text-purple-700 hover:bg-purple-100/60"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Brain size={12} />
        <span className="font-medium">{done ? '思考過程' : '思考中...'}</span>
        {!done && <Loader2 size={10} className="animate-spin" />}
      </button>
      {open && (
        <div className="px-3 py-2 text-xs text-gray-600 whitespace-pre-wrap border-t border-purple-100 max-h-64 overflow-y-auto font-mono">
          {text || '(尚未輸出)'}
        </div>
      )}
    </div>
  )
}

export default function QueryPage() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    const question = input.trim()
    if (!question || loading) return

    setInput('')
    setError('')
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: question },
      { role: 'assistant', content: '', streaming: true },
    ])
    setLoading(true)

    let rawBuffer = ''
    try {
      for await (const ev of queryWikiStream(question)) {
        if (ev.type === 'pages') {
          setMessages((prev) => {
            const copy = [...prev]
            const last = copy[copy.length - 1]
            if (last?.role === 'assistant') last.referenced_pages = ev.pages
            return copy
          })
        } else if (ev.type === 'chunk') {
          rawBuffer += ev.content
          const { thinking, answer, thinkingDone } = splitThinking(rawBuffer)
          setMessages((prev) => {
            const copy = [...prev]
            const last = copy[copy.length - 1]
            if (last?.role === 'assistant') {
              last.content = answer
              last.thinking = thinking
              last.thinkingDone = thinkingDone
            }
            return copy
          })
        } else if (ev.type === 'judge') {
          setMessages((prev) => {
            const copy = [...prev]
            const last = copy[copy.length - 1]
            if (last?.role === 'assistant') {
              last.judge_save = ev.save
              last.judge_reason = ev.reason
            }
            return copy
          })
        } else if (ev.type === 'done') {
          setMessages((prev) => {
            const copy = [...prev]
            const last = copy[copy.length - 1]
            if (last?.role === 'assistant') {
              last.saved_page = ev.saved_page
              last.streaming = false
            }
            return copy
          })
        } else if (ev.type === 'error') {
          throw new Error(ev.message)
        }
      }
    } catch (e: any) {
      setError(e.message || '查詢失敗')
      setMessages((prev) => {
        const copy = [...prev]
        const last = copy[copy.length - 1]
        if (last?.role === 'assistant') last.streaming = false
        return copy
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-screen">
      <div className="p-4 border-b border-gray-200 bg-white flex items-center justify-between">
        <h1 className="font-semibold text-gray-800">查詢 Wiki</h1>
        <span className="flex items-center gap-1.5 text-xs text-gray-500">
          <Brain size={12} className="text-purple-500" />
          自動判斷是否存入 Wiki
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {messages.length === 0 && (
          <div className="text-center py-16 text-gray-400">
            <BookOpen size={40} className="mx-auto mb-3 opacity-30" />
            <p className="font-medium">向你的知識庫提問</p>
            <p className="text-sm mt-1">答案來自你上傳的文件所建立的 wiki</p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-2xl ${msg.role === 'user' ? 'max-w-md' : 'w-full'}`}>
              {msg.role === 'user' ? (
                <div className="bg-blue-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 text-sm">
                  {msg.content}
                </div>
              ) : (
                <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm p-4">
                  {msg.thinking !== undefined && msg.thinking !== '' && (
                    <ThinkingBlock text={msg.thinking} done={!!msg.thinkingDone} />
                  )}

                  {msg.content || !msg.streaming ? (
                    <div className="prose max-w-none text-sm">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                      {msg.streaming && (
                        <span className="inline-block w-2 h-4 bg-blue-400 animate-pulse ml-0.5 align-middle" />
                      )}
                    </div>
                  ) : (
                    <Loader2 size={16} className="animate-spin text-blue-500" />
                  )}

                  {msg.referenced_pages && msg.referenced_pages.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-gray-100">
                      <p className="text-xs text-gray-400 mb-1">參考頁面：</p>
                      <div className="flex flex-wrap gap-1">
                        {msg.referenced_pages.map((p) => (
                          <span
                            key={p.id}
                            className="text-xs bg-blue-50 text-blue-600 px-2 py-0.5 rounded-full"
                          >
                            {p.title}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {msg.judge_save !== undefined && (
                    <div
                      className={`mt-2 flex items-start gap-1 text-xs ${
                        msg.judge_save ? 'text-green-600' : 'text-gray-400'
                      }`}
                    >
                      {msg.judge_save ? <BookmarkPlus size={12} className="mt-0.5 shrink-0" /> : <BookmarkX size={12} className="mt-0.5 shrink-0" />}
                      <span>
                        {msg.judge_save
                          ? `已存入 Wiki${msg.saved_page ? `：${msg.saved_page.title}` : ''}`
                          : '未存入 Wiki'}
                        {msg.judge_reason && <span className="text-gray-400"> — {msg.judge_reason}</span>}
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}

        {error && (
          <p className="text-center text-sm text-red-600">{error}</p>
        )}

        <div ref={bottomRef} />
      </div>

      <div className="p-4 border-t border-gray-200 bg-white">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
            placeholder="問一個問題..."
            className="flex-1 border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
            disabled={loading}
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="bg-blue-600 text-white px-4 py-2.5 rounded-xl hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}
