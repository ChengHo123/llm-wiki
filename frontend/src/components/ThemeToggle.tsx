import { Moon, Sun } from 'lucide-react'
import { useTheme } from '../hooks/useTheme'

export default function ThemeToggle({ className = '' }: { className?: string }) {
  const { theme, toggleTheme } = useTheme()
  const isDark = theme === 'dark'
  return (
    <button
      onClick={toggleTheme}
      title={isDark ? '切換到淺色模式' : '切換到深色模式'}
      className={
        'inline-flex items-center justify-center rounded-lg w-8 h-8 transition-colors ' +
        'text-zinc-500 hover:text-zinc-700 hover:bg-zinc-100 ' +
        'dark:text-zinc-400 dark:hover:text-zinc-100 dark:hover:bg-zinc-800 ' +
        className
      }
    >
      {isDark ? <Sun size={15} /> : <Moon size={15} />}
    </button>
  )
}
