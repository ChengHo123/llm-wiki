import { useState, useEffect, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { Key, Upload, FileText, CheckCircle, XCircle, Clock, RefreshCw, Trash2, RotateCcw, Check } from 'lucide-react'
import {
  createApiKey, uploadDocument, listDocuments, deleteDocument, retryDocument, whoAmI,
  getStoredApiKey, clearStoredApiKey,
  listStoredKeys, addStoredKey, selectStoredKey, removeStoredKey, getActiveKeyName,
  type Document, type StoredKey,
} from '../api/client'

const STATUS_ICON: Record<string, JSX.Element> = {
  done: <CheckCircle size={14} className="text-green-500" />,
  error: <XCircle size={14} className="text-red-500" />,
  processing: <RefreshCw size={14} className="text-blue-500 animate-spin" />,
  queued: <Clock size={14} className="text-yellow-400" />,
  pending: <Clock size={14} className="text-gray-400" />,
}

const STATUS_LABEL: Record<string, string> = {
  done: '完成',
  error: '失敗',
  processing: '處理中',
  queued: '排隊中',
  pending: '等待中',
}

export default function HomePage() {
  const [apiKey, setApiKey] = useState(getStoredApiKey())
  const [activeName, setActiveName] = useState(getActiveKeyName())
  const [keyList, setKeyList] = useState<StoredKey[]>(listStoredKeys())
  const [keyName, setKeyName] = useState('')
  const [newKey, setNewKey] = useState('')
  const [loginKey, setLoginKey] = useState('')
  const [loggingIn, setLoggingIn] = useState(false)
  const [docs, setDocs] = useState<Document[]>([])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  const refreshKeyState = () => {
    setApiKey(getStoredApiKey())
    setActiveName(getActiveKeyName())
    setKeyList(listStoredKeys())
  }

  const loadDocs = useCallback(async () => {
    if (!getStoredApiKey()) return
    try {
      setDocs(await listDocuments())
    } catch {}
  }, [])

  useEffect(() => { loadDocs() }, [loadDocs])

  // 輪詢進行中的文件
  useEffect(() => {
    const hasPending = docs.some((d) => d.status === 'processing' || d.status === 'pending' || d.status === 'queued')
    if (!hasPending) return
    const t = setInterval(loadDocs, 3000)
    return () => clearInterval(t)
  }, [docs, loadDocs])

  const handleCreateKey = async () => {
    const name = keyName.trim()
    if (!name) return
    try {
      const res = await createApiKey(name)
      setNewKey(res.key)
      addStoredKey(name, res.key)
      setKeyName('')
      refreshKeyState()
      loadDocs()
    } catch (e: any) {
      setError(e.response?.data?.detail || '建立失敗')
    }
  }

  const handleSelectKey = (name: string) => {
    if (!selectStoredKey(name)) return
    setNewKey('')
    refreshKeyState()
    loadDocs()
  }

  const handleRemoveKey = (name: string) => {
    if (!confirm(`從瀏覽器移除「${name}」？（後端 key 仍有效，不會刪除資料）`)) return
    removeStoredKey(name)
    refreshKeyState()
    if (getActiveKeyName() === '') setDocs([])
  }

  const handleLogin = async () => {
    const key = loginKey.trim()
    if (!key) return
    setLoggingIn(true)
    setError('')
    try {
      const info = await whoAmI(key)
      addStoredKey(info.name, key)
      setLoginKey('')
      refreshKeyState()
      loadDocs()
    } catch (e: any) {
      setError(e.response?.status === 401 || e.response?.status === 403
        ? '無效的 API Key'
        : (e.response?.data?.detail || '登入失敗'))
    } finally {
      setLoggingIn(false)
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

        {newKey && (
          <div className="bg-yellow-50 border border-yellow-300 rounded-lg p-3 mb-3">
            <p className="text-xs text-yellow-700 mb-1 font-semibold">新 Key 已建立，請複製並儲存（之後不會再顯示）</p>
            <p className="font-mono text-sm break-all text-yellow-900">{newKey}</p>
          </div>
        )}

        {keyList.length > 0 && (
          <div className="mb-4">
            <p className="text-xs text-gray-500 mb-2">已儲存的 Keys（點擊切換）：</p>
            <ul className="space-y-1">
              {keyList.map((k) => {
                const isActive = k.name === activeName
                return (
                  <li
                    key={k.name}
                    className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm border ${
                      isActive
                        ? 'bg-green-50 border-green-300'
                        : 'bg-gray-50 border-gray-200 hover:bg-gray-100 cursor-pointer'
                    }`}
                    onClick={() => !isActive && handleSelectKey(k.name)}
                  >
                    {isActive ? (
                      <Check size={14} className="text-green-500 shrink-0" />
                    ) : (
                      <span className="w-3.5 h-3.5 rounded-full border border-gray-300 shrink-0" />
                    )}
                    <span className="font-medium text-gray-700 shrink-0">{k.name}</span>
                    <span className="text-xs text-gray-400 font-mono truncate flex-1">{k.key}</span>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleRemoveKey(k.name) }}
                      className="text-gray-300 hover:text-red-500 shrink-0"
                      title="從瀏覽器移除"
                    >
                      <Trash2 size={13} />
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        )}

        <div className="space-y-3">
          <div>
            <p className="text-xs text-gray-500 mb-1">建立新 Key：</p>
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
          </div>

          <div>
            <p className="text-xs text-gray-500 mb-1">使用已有 Key 登入：</p>
            <div className="flex gap-2">
              <input
                value={loginKey}
                onChange={(e) => setLoginKey(e.target.value)}
                placeholder="wk_..."
                type="password"
                className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-300"
                onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
                disabled={loggingIn}
              />
              <button
                onClick={handleLogin}
                disabled={loggingIn || !loginKey.trim()}
                className="bg-gray-700 text-white px-4 py-2 rounded-lg text-sm hover:bg-gray-800 disabled:opacity-40"
              >
                {loggingIn ? '驗證中...' : '登入'}
              </button>
            </div>
          </div>

          {apiKey && (
            <button
              onClick={() => { clearStoredApiKey(); refreshKeyState(); setDocs([]) }}
              className="text-xs text-gray-400 hover:text-red-500"
            >
              取消目前選擇（不刪除儲存的 keys）
            </button>
          )}
        </div>
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
                      {STATUS_ICON[doc.status] ?? <Clock size={14} />}
                      <span>{STATUS_LABEL[doc.status] ?? doc.status}</span>
                    </div>
                    {doc.status === 'error' && (
                      <button
                        onClick={async () => {
                          try {
                            await retryDocument(doc.id)
                            await loadDocs()
                          } catch { setError('重試失敗') }
                        }}
                        className="text-gray-300 hover:text-blue-500 transition-colors flex-shrink-0"
                        title="重新處理"
                      >
                        <RotateCcw size={13} />
                      </button>
                    )}
                    <button
                      onClick={async () => {
                        if (!confirm(`刪除「${doc.filename}」及其產生的 wiki 頁面？`)) return
                        try {
                          await deleteDocument(doc.id)
                          await loadDocs()
                        } catch { setError('刪除失敗') }
                      }}
                      className="text-gray-300 hover:text-red-500 transition-colors flex-shrink-0"
                      title="刪除文件與對應 wiki 頁面"
                    >
                      <Trash2 size={13} />
                    </button>
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
