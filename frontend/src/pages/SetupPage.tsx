import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Anchor,
  Eye,
  EyeOff,
  Loader2,
  Terminal,
  ShieldCheck,
  CheckCircle2,
} from 'lucide-react'
import axios from 'axios'
import clsx from 'clsx'

// -------------------------------------------------------------------------- //
// Password strength                                                            //
// -------------------------------------------------------------------------- //

type Strength = 'weak' | 'fair' | 'strong'

function measureStrength(pwd: string): Strength {
  if (pwd.length < 8) return 'weak'
  let score = 0
  if (pwd.length >= 16) score++
  if (/[A-Z]/.test(pwd)) score++
  if (/[0-9]/.test(pwd)) score++
  if (/[^A-Za-z0-9]/.test(pwd)) score++
  if (score <= 1) return 'fair'
  return 'strong'
}

const strengthConfig: Record<Strength, { label: string; bars: number; color: string }> = {
  weak:   { label: 'Weak',   bars: 1, color: 'bg-red-500'    },
  fair:   { label: 'Fair',   bars: 2, color: 'bg-amber-400'  },
  strong: { label: 'Strong', bars: 3, color: 'bg-emerald-500'},
}

function StrengthBar({ password }: { password: string }) {
  if (!password) return null
  const { label, bars, color } = strengthConfig[measureStrength(password)]
  return (
    <div className="mt-2 space-y-1">
      <div className="flex gap-1">
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className={clsx(
              'h-1 flex-1 rounded-full transition-colors duration-300',
              i <= bars ? color : 'bg-gray-700',
            )}
          />
        ))}
      </div>
      <p className={clsx('text-xs', {
        'text-red-400':     label === 'Weak',
        'text-amber-400':   label === 'Fair',
        'text-emerald-400': label === 'Strong',
      })}>{label} password</p>
    </div>
  )
}

// -------------------------------------------------------------------------- //
// Page                                                                         //
// -------------------------------------------------------------------------- //

export default function SetupPage() {
  const [token, setToken]           = useState('')
  const [showToken, setShowToken]   = useState(false)
  const [username, setUsername]     = useState('')
  const [email, setEmail]           = useState('')
  const [password, setPassword]     = useState('')
  const [showPass, setShowPass]     = useState(false)
  const [confirmPwd, setConfirmPwd] = useState('')
  const [showConfirm, setShowConfirm] = useState(false)
  const [error, setError]           = useState('')
  const [loading, setLoading]       = useState(false)
  const [done, setDone]             = useState(false)
  const navigate = useNavigate()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')

    if (password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    if (password !== confirmPwd) {
      setError('Passwords do not match.')
      return
    }

    setLoading(true)
    try {
      const res = await axios.post('/api/setup/complete', {
        token:          token.trim(),
        admin_username: username.trim(),
        admin_email:    email.trim(),
        admin_password: password,
      })
      localStorage.setItem('snapdock_token', res.data.access_token)
      setDone(true)
      setTimeout(() => navigate('/dashboard', { replace: true }), 1200)
    } catch (err: unknown) {
      if (axios.isAxiosError(err)) {
        const status  = err.response?.status
        const detail  = err.response?.data?.detail ?? 'Setup failed.'
        if (status === 410) {
          setError('Setup has already been completed. Please log in instead.')
        } else if (status === 429) {
          setError('Too many failed attempts. Restart the daemon to try again.')
        } else {
          setError(String(detail))
        }
      } else {
        setError('Setup failed — check the daemon logs.')
      }
    } finally {
      setLoading(false)
    }
  }

  // ── Success state ─────────────────────────────────────────────────────────
  if (done) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center p-4">
        <div className="text-center animate-fade-in space-y-4">
          <CheckCircle2 size={48} className="text-emerald-400 mx-auto" />
          <h2 className="text-xl font-semibold text-white">Setup complete!</h2>
          <p className="text-gray-400 text-sm">Redirecting to dashboard…</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center p-4">
      <div className="w-full max-w-sm animate-fade-in">

        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-12 h-12 rounded-2xl bg-brand-600 flex items-center justify-center mx-auto mb-4 shadow-lg shadow-brand-900/50">
            <Anchor size={22} className="text-white" />
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight">
            Snap<span className="text-brand-400">Dock</span>
          </h1>
          <p className="text-gray-500 text-sm mt-1">First-boot setup</p>
        </div>

        {/* Info banner */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 mb-4 flex gap-3">
          <Terminal size={15} className="text-brand-400 mt-0.5 shrink-0" />
          <div className="text-xs text-gray-400 leading-relaxed">
            A one-time setup token was printed to the daemon log on first boot.
            Retrieve it with:{' '}
            <code className="text-gray-200 bg-gray-800 px-1.5 py-0.5 rounded font-mono">
              docker logs snapdock
            </code>
          </div>
        </div>

        {/* Card */}
        <form
          onSubmit={handleSubmit}
          className="bg-gray-900 border border-gray-800 rounded-2xl p-6 space-y-4 shadow-xl"
        >
          {error && (
            <div className="bg-red-950/50 border border-red-800/60 text-red-300 text-sm px-3 py-2.5 rounded-lg">
              {error}
            </div>
          )}

          {/* Setup token */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1.5">
              Setup Token
            </label>
            <div className="relative">
              <input
                type={showToken ? 'text' : 'password'}
                value={token}
                onChange={(e) => setToken(e.target.value)}
                required
                autoFocus
                autoComplete="off"
                spellCheck={false}
                className="w-full bg-gray-800 border border-gray-700 text-gray-100 text-sm rounded-lg px-3 py-2.5 pr-10 font-mono placeholder:text-gray-600 placeholder:font-sans transition-shadow"
                placeholder="Paste token from daemon log"
              />
              <button
                type="button"
                onClick={() => setShowToken((v) => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 transition-colors"
              >
                {showToken ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          {/* Divider */}
          <div className="border-t border-gray-800 pt-1">
            <p className="text-xs font-medium text-gray-500 flex items-center gap-1.5 mb-3">
              <ShieldCheck size={12} className="text-brand-500" />
              Admin account
            </p>

            {/* Username */}
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">
                  Username
                </label>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  autoComplete="username"
                  className="w-full bg-gray-800 border border-gray-700 text-gray-100 text-sm rounded-lg px-3 py-2.5 placeholder:text-gray-600 transition-shadow"
                  placeholder="admin"
                />
              </div>

              {/* Email */}
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">
                  Email
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                  className="w-full bg-gray-800 border border-gray-700 text-gray-100 text-sm rounded-lg px-3 py-2.5 placeholder:text-gray-600 transition-shadow"
                  placeholder="admin@example.com"
                />
              </div>

              {/* Password */}
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">
                  Password
                  <span className="text-gray-600 font-normal ml-1">(min 8 chars)</span>
                </label>
                <div className="relative">
                  <input
                    type={showPass ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    autoComplete="new-password"
                    className="w-full bg-gray-800 border border-gray-700 text-gray-100 text-sm rounded-lg px-3 py-2.5 pr-10 placeholder:text-gray-600 transition-shadow"
                    placeholder="••••••••••••"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPass((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 transition-colors"
                  >
                    {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
                <StrengthBar password={password} />
              </div>

              {/* Confirm password */}
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">
                  Confirm Password
                </label>
                <div className="relative">
                  <input
                    type={showConfirm ? 'text' : 'password'}
                    value={confirmPwd}
                    onChange={(e) => setConfirmPwd(e.target.value)}
                    required
                    autoComplete="new-password"
                    className={clsx(
                      'w-full bg-gray-800 border text-gray-100 text-sm rounded-lg px-3 py-2.5 pr-10 placeholder:text-gray-600 transition-shadow',
                      confirmPwd && password !== confirmPwd
                        ? 'border-red-700/60'
                        : 'border-gray-700',
                    )}
                    placeholder="••••••••••••"
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirm((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 transition-colors"
                  >
                    {showConfirm ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
                {confirmPwd && password !== confirmPwd && (
                  <p className="text-red-400 text-xs mt-1">Passwords do not match</p>
                )}
              </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-brand-600 hover:bg-brand-700 disabled:opacity-60 text-white font-medium text-sm py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2 mt-2"
          >
            {loading ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <ShieldCheck size={14} />
            )}
            Complete Setup
          </button>
        </form>

        <p className="text-center text-xs text-gray-700 mt-5">
          This page is only available during first boot.
        </p>
      </div>
    </div>
  )
}
