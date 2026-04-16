import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { BookOpen, Upload, MessageSquare, Home, Network } from 'lucide-react'
import HomePage from './pages/Home'
import WikiPage from './pages/Wiki'
import QueryPage from './pages/Query'
import GraphPage from './pages/Graph'

function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-gray-50 flex">
      {/* Sidebar */}
      <nav className="w-56 bg-white border-r border-gray-200 flex flex-col flex-shrink-0">
        <div className="p-4 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <BookOpen className="text-blue-600" size={22} />
            <span className="font-bold text-gray-800">LLM Wiki</span>
          </div>
          <p className="text-xs text-gray-400 mt-1">個人知識庫</p>
        </div>
        <div className="flex-1 p-3 space-y-1">
          {[
            { to: '/',      icon: <Home      size={16} />, label: '首頁 / 上傳' },
            { to: '/wiki',  icon: <BookOpen  size={16} />, label: 'Wiki 頁面' },
            { to: '/graph', icon: <Network   size={16} />, label: '知識圖譜' },
            { to: '/query', icon: <MessageSquare size={16} />, label: '查詢' },
          ].map(({ to, icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
                  isActive
                    ? 'bg-blue-50 text-blue-700 font-medium'
                    : 'text-gray-600 hover:bg-gray-100'
                }`
              }
            >
              {icon}
              {label}
            </NavLink>
          ))}
        </div>
      </nav>

      {/* Main */}
      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/"      element={<HomePage />} />
          <Route path="/wiki"  element={<WikiPage />} />
          <Route path="/graph" element={<GraphPage />} />
          <Route path="/query" element={<QueryPage />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
