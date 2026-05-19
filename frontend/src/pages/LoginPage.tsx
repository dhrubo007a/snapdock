import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Eye, EyeOff, Loader2, LogIn } from 'lucide-react'
import axios from 'axios'

export default function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPass, setShowPass] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await axios.post('/api/auth/login', { username, password })
      localStorage.setItem('snapdock_token', res.data.access_token)
      navigate('/dashboard', { replace: true })
    } catch (err: unknown) {
      const msg =
        axios.isAxiosError(err)
          ? (err.response?.data?.detail ?? 'Login failed')
          : 'Login failed'
      setError(String(msg))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex">
      {/* ── Left panel — hero image ─────────────────────────────────────── */}
      <div
        className="hidden lg:flex flex-1 relative overflow-hidden"
        style={{ backgroundImage: 'url(/login_bg.png)', backgroundSize: 'cover', backgroundPosition: 'center' }}
      >
        {/* subtle dark vignette on right edge to blend into form panel */}
        <div className="absolute inset-0 bg-gradient-to-r from-transparent to-gray-950/80" />
      </div>

      {/* ── Right panel — form ──────────────────────────────────────────── */}
      <div className="w-full lg:w-[420px] flex flex-col justify-center bg-gray-950 px-8 py-12 relative">
        {/* Mobile-only faint bg */}
        <div
          className="lg:hidden absolute inset-0 opacity-10"
          style={{ backgroundImage: 'url(/login_bg.png)', backgroundSize: 'cover', backgroundPosition: 'center' }}
        />

        <div className="relative z-10 w-full max-w-sm mx-auto animate-fade-in">
          {/* Logo */}
          <div className="mb-10 text-center">
            <img
              src="/logo.png"
              alt="SnapDock"
              className="h-28 w-auto max-w-[220px] mx-auto mb-5 drop-shadow-lg object-contain"
            />
            <h1 className="text-2xl font-bold text-white tracking-tight leading-tight text-center">
              Welcome back
            </h1>
            <p className="text-gray-500 text-sm mt-1 text-center">
              Sign in to your SnapDock instance
            </p>
          </div>

          {/* Error */}
          {error && (
            <div className="mb-4 bg-red-950/50 border border-red-800/60 text-red-300 text-sm px-4 py-3 rounded-xl">
              {error}
            </div>
          )}

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">
                Username or Email
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoFocus
                autoComplete="username"
                className="w-full bg-gray-900 border border-gray-800 text-gray-100 text-sm rounded-xl px-4 py-3 placeholder:text-gray-600 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500/30 transition"
                placeholder="admin"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">
                Password
              </label>
              <div className="relative">
                <input
                  type={showPass ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                  className="w-full bg-gray-900 border border-gray-800 text-gray-100 text-sm rounded-xl px-4 py-3 pr-11 placeholder:text-gray-600 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500/30 transition"
                  placeholder="••••••••"
                />
                <button
                  type="button"
                  onClick={() => setShowPass((v) => !v)}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 transition-colors"
                  tabIndex={-1}
                >
                  {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-brand-600 hover:bg-brand-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold text-sm py-3 rounded-xl transition-colors flex items-center justify-center gap-2 mt-2 shadow-lg shadow-brand-900/40"
            >
              {loading
                ? <Loader2 size={16} className="animate-spin" />
                : <LogIn size={16} />
              }
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>

          <p className="text-center text-xs text-gray-700 mt-8">
            SnapDock · Snapshot Manager for Docker Containers
          </p>
        </div>
      </div>
    </div>
  )
}
