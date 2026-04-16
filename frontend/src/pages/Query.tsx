import { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Send, BookmarkPlus, BookOpen, Loader2 } from 'lucide-react'
import { queryWiki, type QueryResult } from '../api/client'

interface Message {
  role: 'user' | 'assistant'
  content: string
  referenced_pages?: { id: string; title: string; slug: string }[]
  saved_page?: { id: string; title: string; slug: string } | null
}

export default function QueryPage() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [saveToWiki, setSaveToWiki] = useState(false)
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
    setMessages((prev) => [...prev, { role: 'user', content: question }])
    setLoading(true)

    try {
      const result = await queryWiki(question, saveToWiki)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: result.answer,
          referenced_pages: result.referenced_pages,
          saved_page: result.saved_page,
        },
      ])
    } catch (e: any) {
      setError(e.response?.data?.detail || '查詢失敗')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <div className="p-4 border-b border-gray-200 bg-white flex items-center justify-between">
        <h1 className="font-semibold text-gray-800">查詢 Wiki</h1>
        <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
          <input
            type="checkbox"
            checked={saveToWiki}
            onChange={(e) => setSaveToWiki(e.target.checked)}
            className="rounded"
          />
          <BookmarkPlus size={14} />
          回答存入 Wiki
        </label>
      </div>

      {/* Messages */}
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
                  <div className="prose max-w-none text-sm">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  </div>

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

                  {msg.saved_page && (
                    <div className="mt-2 flex items-center gap-1 text-xs text-green-600">
                      <BookmarkPlus size={12} />
                      <span>已存入 Wiki：{msg.saved_page.title}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3">
              <Loader2 size={16} className="animate-spin text-blue-500" />
            </div>
          </div>
        )}

        {error && (
          <p className="text-center text-sm text-red-600">{error}</p>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
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
