import { useState, useEffect, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { Key, Upload, FileText, CheckCircle, XCircle, Clock, RefreshCw } from 'lucide-react'
import {
  createApiKey, uploadDocument, listDocuments,
  getStoredApiKey, setStoredApiKey, clearStoredApiKey,
  type Document,
} from '../api/client'

const STATUS_ICON = {
  done: <CheckCircle size={14} className="text-green-500" />,
  error: <XCircle size={14} className="text-red-500" />,
  processing: <RefreshCw size={14} className="text-blue-500 animate-spin" />,
  pending: <Clock size={14} className="text-gray-400" />,
}

export default function HomePage() {
  const [apiKey, setApiKey] = useState(getStoredApiKey())
  const [keyName, setKeyName] = useState('')
  const [newKey, setNewKey] = useState('')
  const [docs, setDocs] = useState<Document[]>([])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  const loadDocs = useCallback(async () => {
    if (!getStoredApiKey()) return
    try {
      setDocs(await listDocuments())
    } catch {}
  }, [])

  useEffect(() => { loadDocs() }, [loadDocs])

  // 輪詢進行中的文件
  useEffect(() => {
    const hasPending = docs.some((d) => d.status === 'processing' || d.status === 'pending')
    if (!hasPending) return
    const t = setInterval(loadDocs, 3000)
    return () => clearInterval(t)
  }, [docs, loadDocs])

  const handleCreateKey = async () => {
    if (!keyName.trim()) return
    try {
      const res = await createApiKey(keyName)
      setNewKey(res.key)
      setStoredApiKey(res.key)
      setApiKey(res.key)
      loadDocs()
    } catch (e: any) {
      setError(e.response?.data?.detail || '建立失敗')
    }
  }

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    if (!getStoredApiKey()) { setError('請先設定 API Key'); return }
    setUploading(true)
    setError('')
    try {
      for (const file of acceptedFiles) {
        await uploadDocument(file)
      }
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

  return (
    <div className="p-8 max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-800 mb-6">設定 & 上傳文件</h1>

      {/* API Key 區塊 */}
      <section className="bg-white rounded-xl border border-gray-200 p-5 mb-6">
        <div className="flex items-center gap-2 mb-4">
          <Key size={16} className="text-blue-600" />
          <h2 className="font-semibold text-gray-700">API Key</h2>
        </div>

        {apiKey ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
              <CheckCircle size={14} className="text-green-500" />
              <span className="text-sm text-green-700 font-mono truncate">{apiKey}</span>
            </div>
            <button
              onClick={() => { clearStoredApiKey(); setApiKey(''); setDocs([]) }}
              className="text-xs text-red-500 hover:text-red-700"
            >
              清除 Key
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            {newKey && (
              <div className="bg-yellow-50 border border-yellow-300 rounded-lg p-3">
                <p className="text-xs text-yellow-700 mb-1 font-semibold">請複製並儲存此 Key（之後不會再顯示）</p>
                <p className="font-mono text-sm break-all text-yellow-900">{newKey}</p>
              </div>
            )}
            <div className="flex gap-2">
              <input
                value={keyName}
                onChange={(e) => setKeyName(e.target.value)}
                placeholder="Key 名稱（例如：我的電腦）"
                className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
                onKeyDown={(e) => e.key === 'Enter' && handleCreateKey()}
              />
              <button
                onClick={handleCreateKey}
                className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-blue-700"
              >
                建立
              </button>
            </div>
            <p className="text-xs text-gray-400">或直接輸入已有的 API Key：</p>
            <div className="flex gap-2">
              <input
                placeholder="wk_..."
                className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-300"
                onBlur={(e) => { if (e.target.value) { setStoredApiKey(e.target.value); setApiKey(e.target.value); loadDocs() } }}
              />
            </div>
          </div>
        )}
      </section>

      {/* 上傳區塊 */}
      {apiKey && (
        <>
          <section className="mb-6">
            <div
              {...getRootProps()}
              className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
                isDragActive ? 'border-blue-400 bg-blue-50' : 'border-gray-300 hover:border-blue-300 hover:bg-gray-50'
              }`}
            >
              <input {...getInputProps()} />
              <Upload size={28} className="mx-auto mb-3 text-gray-400" />
              {uploading ? (
                <p className="text-blue-600 font-medium">上傳中...</p>
              ) : isDragActive ? (
                <p className="text-blue-600 font-medium">放開以上傳</p>
              ) : (
                <>
                  <p className="text-gray-600 font-medium mb-1">拖放文件至此，或點擊選擇</p>
                  <p className="text-xs text-gray-400">支援 PDF、圖片、TXT、Markdown</p>
                </>
              )}
            </div>
          </section>

          {/* 文件列表 */}
          {docs.length > 0 && (
            <section className="bg-white rounded-xl border border-gray-200 p-5">
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-semibold text-gray-700">已上傳文件</h2>
                <button onClick={loadDocs} className="text-gray-400 hover:text-gray-600">
                  <RefreshCw size={14} />
                </button>
              </div>
              <ul className="space-y-2">
                {docs.map((doc) => (
                  <li key={doc.id} className="flex items-center gap-3 py-2 border-b border-gray-100 last:border-0">
                    <FileText size={14} className="text-gray-400 flex-shrink-0" />
                    <span className="flex-1 text-sm text-gray-700 truncate">{doc.filename}</span>
                    <div className="flex items-center gap-1 text-xs text-gray-500">
                      {STATUS_ICON[doc.status as keyof typeof STATUS_ICON]}
                      <span>{doc.status}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      {error && (
        <p className="mt-4 text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>
      )}
    </div>
  )
}
