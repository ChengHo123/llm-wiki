import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { addStoredKey } from './api/client'

// LINE Bot push 給用戶的 ?token=ws_... 連結，自動登入
function consumeUrlToken() {
  const params = new URLSearchParams(window.location.search)
  const token = params.get('token')
  if (!token || !token.startsWith('ws_')) return
  const name = params.get('name') || 'LINE'
  addStoredKey(name, token)
  params.delete('token')
  params.delete('name')
  const qs = params.toString()
  const newUrl = window.location.pathname + (qs ? `?${qs}` : '') + window.location.hash
  window.history.replaceState({}, '', newUrl)
}

consumeUrlToken()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
