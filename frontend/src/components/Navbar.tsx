import { Link, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

export default function Navbar() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const location = useLocation()

  const links = [
    { to: '/', label: '血缘工作台' },
    { to: '/databases', label: '数据库' },
    { to: '/tables', label: '表结构' },
    { to: '/nodes', label: '节点搜索' },
  ]

  return (
    <nav className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
      <div className="flex items-center gap-6">
        <Link to="/" className="text-lg font-bold text-blue-700">
          SQLineage
        </Link>
        {links.map((l) => (
          <Link
            key={l.to}
            to={l.to}
            className={`text-sm ${
              location.pathname === l.to
                ? 'text-blue-600 font-medium'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            {l.label}
          </Link>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <span className="text-xs text-gray-400 bg-gray-100 px-2 py-1 rounded">
          {user?.role}
        </span>
        <span className="text-sm text-gray-700">{user?.username}</span>
        <button
          onClick={logout}
          className="text-sm text-gray-500 hover:text-red-600"
        >
          登出
        </button>
      </div>
    </nav>
  )
}
