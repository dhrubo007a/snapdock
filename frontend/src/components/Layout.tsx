import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { LayoutDashboard, Archive, Shield, Settings, LogOut, ChevronRight, ClipboardList } from 'lucide-react'
import clsx from 'clsx'
import api from '../lib/api'

const navItems = [
  { to: '/dashboard', label: 'Dashboard', Icon: LayoutDashboard },
  { to: '/snapshots',  label: 'Snapshots',  Icon: Archive },
  { to: '/coverage',  label: 'Coverage',   Icon: Shield },
  { to: '/audit',     label: 'Audit Log',  Icon: ClipboardList },
  { to: '/settings',  label: 'Settings',   Icon: Settings },
]

export default function Layout() {
  const navigate = useNavigate()

  async function handleLogout() {
    try {
      await api.post('/auth/logout')
    } catch {
      // Ignore errors — token is already invalid or network is down
    } finally {
      localStorage.removeItem('snapdock_token')
      navigate('/login', { replace: true })
    }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-gray-950">
      {/* Sidebar */}
      <aside className="w-56 flex flex-col border-r border-gray-800/60 bg-gray-950 shrink-0">
        {/* Logo */}
        <div className="h-14 px-4 flex items-center gap-2.5 border-b border-gray-800/60">
          <img src="/logo.png" alt="SnapDock" className="h-8 w-auto object-contain shrink-0" />
          <span className="text-[15px] font-semibold text-gray-100 tracking-tight">SnapDock</span>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-2.5 py-3 space-y-0.5 overflow-y-auto">
          {navItems.map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                clsx(
                  'flex items-center justify-between px-3 py-2 rounded-lg text-[13px] font-medium transition-all duration-150',
                  isActive
                    ? 'bg-brand-500/10 text-brand-400 ring-1 ring-inset ring-brand-500/20'
                    : 'text-gray-400 hover:bg-gray-800/70 hover:text-gray-200',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span className="flex items-center gap-2.5">
                    <Icon size={14} />
                    {label}
                  </span>
                  {isActive && <ChevronRight size={11} className="text-brand-500/70" />}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div className="px-2.5 pb-3 border-t border-gray-800/60 pt-2.5">
          <button
            onClick={handleLogout}
            className="w-full flex items-center gap-2.5 px-3 py-2 text-[13px] text-gray-600 hover:text-gray-300 rounded-lg hover:bg-gray-800/60 transition-colors"
          >
            <LogOut size={13} />
            Sign out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        <div className="p-6 min-h-full">
          <Outlet />
        </div>
      </main>
    </div>
  )
}

