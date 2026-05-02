import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { BookOpen, MessageSquare, Home, Network } from 'lucide-react'
import HomePage from './pages/Home'
import WikiPage from './pages/Wiki'
import QueryPage from './pages/Query'
import GraphPage from './pages/Graph'
import MobilePage from './pages/Mobile'
import AdminLogin from './pages/AdminLogin'
import AdminUsers from './pages/AdminUsers'
import AdminUserDetail from './pages/AdminUserDetail'
import AdminOverview from './pages/AdminOverview'
import AdminLogs from './pages/AdminLogs'
import ThemeToggle from './components/ThemeToggle'

function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-gray-50 dark:bg-zinc-950 flex">
      {/* Sidebar */}
      <nav className="w-56 bg-white dark:bg-zinc-900 border-r border-gray-200 dark:border-zinc-700 flex flex-col flex-shrink-0">
        <div className="p-4 border-b border-gray-200 dark:border-zinc-700">
          <div className="flex items-center gap-2">
            <BookOpen className="text-blue-600" size={22} />
            <span className="font-bold text-gray-800 dark:text-zinc-100">LLM Wiki</span>
          </div>
          <p className="text-xs text-gray-400 dark:text-zinc-500 mt-1">個人知識庫</p>
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
                    ? 'bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300 font-medium'
                    : 'text-gray-600 dark:text-zinc-400 hover:bg-gray-100 dark:hover:bg-zinc-800'
                }`
              }
            >
              {icon}
              {label}
            </NavLink>
          ))}
        </div>
        <div className="p-3 border-t border-gray-200 dark:border-zinc-700 flex justify-end">
          <ThemeToggle />
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
      <Routes>
        {/* Mobile route — no sidebar */}
        <Route path="/m" element={<MobilePage />} />

        {/* Admin routes — no sidebar */}
        <Route path="/admin/login" element={<AdminLogin />} />
        <Route path="/admin" element={<AdminOverview />} />
        <Route path="/admin/overview" element={<AdminOverview />} />
        <Route path="/admin/users" element={<AdminUsers />} />
        <Route path="/admin/users/:id" element={<AdminUserDetail />} />
        <Route path="/admin/logs" element={<AdminLogs />} />

        {/* Desktop routes */}
        <Route path="/"      element={<Layout><HomePage /></Layout>} />
        <Route path="/wiki"  element={<Layout><WikiPage /></Layout>} />
        <Route path="/graph" element={<Layout><GraphPage /></Layout>} />
        <Route path="/query" element={<Layout><QueryPage /></Layout>} />
      </Routes>
    </BrowserRouter>
  )
}
