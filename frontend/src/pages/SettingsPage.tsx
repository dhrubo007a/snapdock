import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Settings, Users, Bell, Shield, Plus, Pencil, Trash2,
  KeyRound, Eye, EyeOff, X, Check, AlertCircle, Loader2, Copy,
} from 'lucide-react'
import clsx from 'clsx'
import api from '../lib/api'
import Modal from '../components/Modal'

// ── Types ──────────────────────────────────────────────────────────────────

interface SettingsGeneral {
  jwt_expire_minutes: number
  quiesce_timeout: number
  health_check_timeout: number
  stop_timeout: number
  quiesce_overrides: Record<string, string>
}

interface SettingsNotificationsRead {
  webhook_urls: string[]
  smtp_host: string
  smtp_port: number
  smtp_user: string
  smtp_password_configured: boolean
  smtp_from: string
  smtp_to: string[]
}

interface SettingsResponse {
  general: SettingsGeneral
  notifications: SettingsNotificationsRead
}

interface User {
  id: string
  username: string | null
  email: string
  role: string
  is_active: boolean
  created_at: string
}

// ── Shared UI primitives ───────────────────────────────────────────────────

function Label({ children }: { children: React.ReactNode }) {
  return <label className="block text-[12px] font-medium text-gray-400 mb-1">{children}</label>
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={clsx(
        'w-full bg-gray-900 border border-gray-700 text-gray-100 rounded-lg px-3 py-2',
        'text-[13px] placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500/60',
        props.className,
      )}
    />
  )
}

function Btn({
  children, variant = 'primary', loading = false, ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger'; loading?: boolean }) {
  return (
    <button
      {...props}
      disabled={props.disabled || loading}
      className={clsx(
        'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium transition-colors disabled:opacity-40',
        variant === 'primary' && 'bg-brand-600 text-white hover:bg-brand-500',
        variant === 'ghost' && 'bg-gray-800 text-gray-300 hover:bg-gray-700',
        variant === 'danger' && 'bg-red-900/40 text-red-400 hover:bg-red-900/70',
        props.className,
      )}
    >
      {loading && <Loader2 size={12} className="animate-spin" />}
      {children}
    </button>
  )
}

function Toast({ message, type }: { message: string; type: 'success' | 'error' }) {
  return (
    <div
      className={clsx(
        'fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-xl px-4 py-3 text-[13px] shadow-xl border',
        type === 'success' ? 'bg-emerald-900/80 border-emerald-700 text-emerald-200' : 'bg-red-900/80 border-red-700 text-red-200',
      )}
    >
      {type === 'success' ? <Check size={14} /> : <AlertCircle size={14} />}
      {message}
    </div>
  )
}

// ── General Settings Tab ───────────────────────────────────────────────────

const QUIESCE_METHODS = [
  { value: 'auto',                    label: 'Auto-detect (default)' },
  { value: 'postgresql_checkpoint',   label: 'PostgreSQL — CHECKPOINT' },
  { value: 'mysql_flush_tables',      label: 'MySQL / MariaDB — FLUSH TABLES' },
  { value: 'redis_bgsave',            label: 'Redis — BGSAVE' },
  { value: 'mongodb_fsynclock',       label: 'MongoDB — fsyncLock' },
  { value: 'skip',                    label: 'Skip (no quiesce)' },
]

function GeneralTab({ data }: { data: SettingsResponse }) {
  const qc = useQueryClient()
  const [form, setForm] = useState<SettingsGeneral>(data.general)
  const [overrideRows, setOverrideRows] = useState<{ service: string; method: string }[]>(
    () => Object.entries(data.general.quiesce_overrides ?? {}).map(([service, method]) => ({ service, method }))
  )
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null)

  useEffect(() => {
    setForm(data.general)
    setOverrideRows(Object.entries(data.general.quiesce_overrides ?? {}).map(([service, method]) => ({ service, method })))
  }, [data.general])

  const showToast = (msg: string, type: 'success' | 'error') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3500)
  }

  const save = useMutation({
    mutationFn: () => {
      const quiesce_overrides = Object.fromEntries(
        overrideRows.filter((r) => r.service.trim()).map((r) => [r.service.trim(), r.method])
      )
      return api.patch('/settings', { general: { ...form, quiesce_overrides } })
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); showToast('Settings saved', 'success') },
    onError: () => showToast('Failed to save settings', 'error'),
  })

  function field(key: keyof Omit<SettingsGeneral, 'quiesce_overrides'>, label: string, unit: string) {
    return (
      <div>
        <Label>{label}</Label>
        <div className="flex items-center gap-2">
          <Input
            type="number"
            min={1}
            value={form[key] as number}
            onChange={(e) => setForm({ ...form, [key]: Number(e.target.value) })}
            className="w-32"
          />
          <span className="text-[12px] text-gray-500">{unit}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6 max-w-lg">
      {toast && <Toast message={toast.msg} type={toast.type} />}
      <div>
        <h3 className="text-[13px] font-semibold text-gray-200 mb-4">Authentication</h3>
        {field('jwt_expire_minutes', 'Session duration', 'minutes')}
      </div>

      <div>
        <h3 className="text-[13px] font-semibold text-gray-200 mb-4">Snapshot Behavior</h3>
        <div className="grid grid-cols-1 gap-4">
          {field('quiesce_timeout', 'Quiesce timeout', 'seconds')}
          {field('health_check_timeout', 'Health-check timeout', 'seconds')}
          {field('stop_timeout', 'Stop timeout', 'seconds')}
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-[13px] font-semibold text-gray-200">Quiesce Overrides</h3>
            <p className="text-[11px] text-gray-600 mt-0.5">
              Force a specific quiesce method per service instead of auto-detecting from the image name.
            </p>
          </div>
          <Btn
            variant="ghost"
            onClick={() => setOverrideRows((r) => [...r, { service: '', method: 'auto' }])}
          >
            <Plus size={11} /> Add
          </Btn>
        </div>
        {overrideRows.length === 0 ? (
          <p className="text-[12px] text-gray-700 italic">No overrides configured — all services use auto-detection.</p>
        ) : (
          <div className="space-y-2">
            {overrideRows.map((row, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  value={row.service}
                  onChange={(e) => setOverrideRows((rs) => rs.map((r, j) => j === i ? { ...r, service: e.target.value } : r))}
                  placeholder="service name"
                  className="flex-1"
                />
                <select
                  value={row.method}
                  onChange={(e) => setOverrideRows((rs) => rs.map((r, j) => j === i ? { ...r, method: e.target.value } : r))}
                  className="bg-gray-900 border border-gray-700 text-gray-200 rounded-lg px-2 py-2 text-[12px] focus:outline-none focus:ring-2 focus:ring-brand-500/40"
                >
                  {QUIESCE_METHODS.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
                <button
                  onClick={() => setOverrideRows((rs) => rs.filter((_, j) => j !== i))}
                  className="p-1.5 rounded-md bg-gray-800 hover:bg-red-900/50 text-gray-500 hover:text-red-400 transition-colors"
                >
                  <X size={11} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <Btn onClick={() => save.mutate()} loading={save.isPending}>
        Save Changes
      </Btn>
    </div>
  )
}

// ── Notifications Tab ──────────────────────────────────────────────────────

function NotificationsTab({ data }: { data: SettingsResponse }) {
  const qc = useQueryClient()
  const n = data.notifications
  const [webhooks, setWebhooks] = useState(n.webhook_urls.join(', '))
  const [smtpHost, setSmtpHost] = useState(n.smtp_host)
  const [smtpPort, setSmtpPort] = useState(String(n.smtp_port))
  const [smtpUser, setSmtpUser] = useState(n.smtp_user)
  const [smtpPass, setSmtpPass] = useState('')
  const [smtpFrom, setSmtpFrom] = useState(n.smtp_from)
  const [smtpTo, setSmtpTo] = useState(n.smtp_to.join(', '))
  const [showPass, setShowPass] = useState(false)
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null)

  useEffect(() => {
    setWebhooks(n.webhook_urls.join(', '))
    setSmtpHost(n.smtp_host); setSmtpPort(String(n.smtp_port))
    setSmtpUser(n.smtp_user); setSmtpFrom(n.smtp_from)
    setSmtpTo(n.smtp_to.join(', '))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.notifications])

  const showToast = (msg: string, type: 'success' | 'error') => {
    setToast({ msg, type }); setTimeout(() => setToast(null), 3500)
  }

  const save = useMutation({
    mutationFn: () =>
      api.patch('/settings', {
        notifications: {
          webhook_urls: webhooks.split(',').map((s) => s.trim()).filter(Boolean),
          smtp_host: smtpHost,
          smtp_port: Number(smtpPort),
          smtp_user: smtpUser,
          smtp_password: smtpPass,
          smtp_from: smtpFrom,
          smtp_to: smtpTo.split(',').map((s) => s.trim()).filter(Boolean),
        },
      }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSmtpPass(''); showToast('Notifications saved', 'success') },
    onError: () => showToast('Failed to save', 'error'),
  })

  return (
    <div className="space-y-6 max-w-lg">
      {toast && <Toast message={toast.msg} type={toast.type} />}
      <div>
        <h3 className="text-[13px] font-semibold text-gray-200 mb-4">Webhooks</h3>
        <Label>Webhook URLs <span className="text-gray-600">(comma-separated)</span></Label>
        <Input
          value={webhooks}
          onChange={(e) => setWebhooks(e.target.value)}
          placeholder="https://hooks.slack.com/... , https://..."
        />
      </div>

      <div>
        <h3 className="text-[13px] font-semibold text-gray-200 mb-4">SMTP Email</h3>
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <Label>Host</Label>
            <Input value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} placeholder="smtp.example.com" />
          </div>
          <div>
            <Label>Port</Label>
            <Input type="number" value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} />
          </div>
          <div>
            <Label>Username</Label>
            <Input value={smtpUser} onChange={(e) => setSmtpUser(e.target.value)} autoComplete="off" />
          </div>
          <div className="col-span-2">
            <Label>
              Password{' '}
              {n.smtp_password_configured && (
                <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-emerald-900/40 text-emerald-400 border border-emerald-800">configured</span>
              )}
            </Label>
            <div className="relative">
              <Input
                type={showPass ? 'text' : 'password'}
                value={smtpPass}
                onChange={(e) => setSmtpPass(e.target.value)}
                placeholder={n.smtp_password_configured ? '(unchanged)' : 'Enter password'}
                autoComplete="new-password"
                className="pr-9"
              />
              <button
                type="button"
                onClick={() => setShowPass((v) => !v)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
              >
                {showPass ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          </div>
          <div>
            <Label>From address</Label>
            <Input type="email" value={smtpFrom} onChange={(e) => setSmtpFrom(e.target.value)} placeholder="alerts@example.com" />
          </div>
          <div>
            <Label>To addresses <span className="text-gray-600">(comma-separated)</span></Label>
            <Input value={smtpTo} onChange={(e) => setSmtpTo(e.target.value)} placeholder="admin@example.com" />
          </div>
        </div>
      </div>

      <Btn onClick={() => save.mutate()} loading={save.isPending}>
        Save Changes
      </Btn>
    </div>
  )
}

// ── Users Tab ──────────────────────────────────────────────────────────────

interface UserModalState {
  mode: 'create' | 'edit' | 'password'
  user?: User
}

function UsersTab() {
  const qc = useQueryClient()
  const { data: me } = useQuery<User>({ queryKey: ['me'], queryFn: () => api.get('/auth/me').then((r) => r.data) })
  const { data: users = [], isLoading } = useQuery<User[]>({ queryKey: ['users'], queryFn: () => api.get('/users').then((r) => r.data) })
  const [modal, setModal] = useState<UserModalState | null>(null)
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null)

  const showToast = (msg: string, type: 'success' | 'error') => {
    setToast({ msg, type }); setTimeout(() => setToast(null), 3500)
  }

  const deleteUser = useMutation({
    mutationFn: (id: string) => api.delete(`/users/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['users'] }); showToast('User deleted', 'success') },
    onError: () => showToast('Failed to delete user', 'error'),
  })

  if (isLoading) return <div className="text-gray-500 text-[13px]">Loading...</div>

  return (
    <div className="space-y-4">
      {toast && <Toast message={toast.msg} type={toast.type} />}
      {modal && (
        <UserModal
          state={modal}
          me={me}
          onClose={() => setModal(null)}
          onSuccess={(msg) => { qc.invalidateQueries({ queryKey: ['users'] }); setModal(null); showToast(msg, 'success') }}
          onError={(msg) => showToast(msg, 'error')}
        />
      )}

      <div className="flex items-center justify-between">
        <h3 className="text-[13px] font-semibold text-gray-200">Users</h3>
        <Btn onClick={() => setModal({ mode: 'create' })}>
          <Plus size={12} /> Add User
        </Btn>
      </div>

      <div className="rounded-xl border border-gray-800 overflow-hidden">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900/60">              <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Username</th>              <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Email</th>
              <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Role</th>
              <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Status</th>
              <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Created</th>
              <th className="px-4 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-b border-gray-800/60 hover:bg-gray-900/40 transition-colors">
                <td className="px-4 py-3 text-gray-200">
                  {u.username ?? u.email}
                  {me?.id === u.id && <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] bg-brand-900/40 text-brand-400 border border-brand-800">you</span>}
                </td>
                <td className="px-4 py-3 text-gray-500 text-[11px]">{u.email}</td>
                <td className="px-4 py-3">
                  <span className={clsx(
                    'px-2 py-0.5 rounded text-[10px] font-medium border',
                    u.role === 'admin'
                      ? 'bg-purple-900/30 text-purple-400 border-purple-800'
                      : 'bg-gray-800 text-gray-400 border-gray-700',
                  )}>
                    {u.role}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={clsx(
                    'px-2 py-0.5 rounded text-[10px] font-medium border',
                    u.is_active
                      ? 'bg-emerald-900/30 text-emerald-400 border-emerald-800'
                      : 'bg-red-900/30 text-red-400 border-red-800',
                  )}>
                    {u.is_active ? 'active' : 'disabled'}
                  </span>
                </td>
                <td className="px-4 py-3 text-gray-500">{new Date(u.created_at).toLocaleDateString()}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-1 justify-end">
                    <button
                      title="Edit user"
                      onClick={() => setModal({ mode: 'edit', user: u })}
                      className="p-1.5 text-gray-500 hover:text-gray-200 rounded-lg hover:bg-gray-700/60 transition-colors"
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      title="Change password"
                      onClick={() => setModal({ mode: 'password', user: u })}
                      className="p-1.5 text-gray-500 hover:text-gray-200 rounded-lg hover:bg-gray-700/60 transition-colors"
                    >
                      <KeyRound size={12} />
                    </button>
                    <button
                      title="Delete user"
                      disabled={me?.id === u.id}
                      onClick={() => {
                        if (confirm(`Delete ${u.username ?? u.email}? This cannot be undone.`)) {
                          deleteUser.mutate(u.id)
                        }
                      }}
                      className="p-1.5 text-gray-500 hover:text-red-400 rounded-lg hover:bg-red-900/30 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* My Account section ─ API key management */}
      {me && <MyAccountSection me={me} />}
    </div>
  )
}

// ── My Account / API Keys ──────────────────────────────────────────────────

interface ApiKey {
  id: string
  name: string
  key_prefix: string | null
  created_at: string
  last_used_at: string | null
}

function MyAccountSection({ me }: { me: User }) {
  const qc = useQueryClient()
  const { data: keys = [] } = useQuery<ApiKey[]>({
    queryKey: ['apikeys', me.id],
    queryFn: () => api.get(`/users/${me.id}/api-keys`).then((r) => r.data),
  })
  const [newKeyName, setNewKeyName] = useState('')
  const [createdSecret, setCreatedSecret] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null)

  const showToast = (msg: string, type: 'success' | 'error') => {
    setToast({ msg, type }); setTimeout(() => setToast(null), 3500)
  }

  function handleCopy() {
    if (!createdSecret) return
    navigator.clipboard.writeText(createdSecret).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const createKey = useMutation({
    mutationFn: () => api.post(`/users/${me.id}/api-keys`, { name: newKeyName || 'My API key' }),
    onSuccess: (r) => {
      setCreatedSecret(r.data.raw_key)
      setCopied(false)
      setNewKeyName('')
      qc.invalidateQueries({ queryKey: ['apikeys', me.id] })
    },
    onError: () => showToast('Failed to create key', 'error'),
  })

  const revokeKey = useMutation({
    mutationFn: (keyId: string) => api.delete(`/users/${me.id}/api-keys/${keyId}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['apikeys', me.id] }); showToast('Key revoked', 'success') },
    onError: () => showToast('Failed to revoke key', 'error'),
  })

  return (
    <div className="mt-8 pt-6 border-t border-gray-800">
      {toast && <Toast message={toast.msg} type={toast.type} />}
      <h3 className="text-[13px] font-semibold text-gray-200 mb-4">My API Keys</h3>

      {/* API key reveal modal */}
      {createdSecret && (
        <Modal
          title="API Key Created"
          onClose={() => setCreatedSecret(null)}
          footer={
            <button
              onClick={() => setCreatedSecret(null)}
              className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-lg transition-colors"
            >
              Done
            </button>
          }
        >
          <div className="space-y-4">
            <div className="flex items-start gap-2.5 rounded-lg bg-amber-900/20 border border-amber-700/50 px-3 py-2.5">
              <AlertCircle size={14} className="text-amber-400 shrink-0 mt-0.5" />
              <p className="text-[12px] text-amber-300 leading-relaxed">
                Copy this key now. For security, it will <strong>never be shown again</strong> after you close this dialog.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 min-w-0 bg-gray-950 border border-gray-800 rounded-lg px-3 py-2.5 text-[12px] text-gray-100 font-mono break-all select-all">
                {createdSecret}
              </code>
              <button
                onClick={handleCopy}
                title="Copy to clipboard"
                className={clsx(
                  'shrink-0 flex items-center gap-1.5 px-3 py-2.5 rounded-lg text-[12px] font-medium transition-all',
                  copied
                    ? 'bg-green-600/20 text-green-400 border border-green-600/40'
                    : 'bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700',
                )}
              >
                {copied ? <Check size={13} /> : <Copy size={13} />}
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
          </div>
        </Modal>
      )}

      <div className="flex gap-2 mb-4">
        <Input
          value={newKeyName}
          onChange={(e) => setNewKeyName(e.target.value)}
          placeholder="Key name (optional)"
          className="max-w-xs"
        />
        <Btn onClick={() => createKey.mutate()} loading={createKey.isPending}>
          <Plus size={12} /> Create Key
        </Btn>
      </div>

      {keys.length > 0 && (
        <div className="rounded-xl border border-gray-800 overflow-hidden">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="border-b border-gray-800 bg-gray-900/60">
                <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Name</th>
                <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Prefix</th>
                <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Last used</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id} className="border-b border-gray-800/60">
                  <td className="px-4 py-3 text-gray-200">{k.name}</td>
                  <td className="px-4 py-3 font-mono text-gray-400">{k.key_prefix ? `${k.key_prefix}…` : <span className="text-gray-700">—</span>}</td>
                  <td className="px-4 py-3 text-gray-500">{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : '—'}</td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => { if (confirm('Revoke this API key?')) revokeKey.mutate(k.id) }}
                      className="p-1.5 text-gray-500 hover:text-red-400 rounded-lg hover:bg-red-900/30 transition-colors"
                    >
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── User Modal (create / edit / change-password) ───────────────────────────

function UserModal({
  state, me, onClose, onSuccess, onError,
}: {
  state: UserModalState
  me?: User
  onClose: () => void
  onSuccess: (msg: string) => void
  onError: (msg: string) => void
}) {
  const isCreate = state.mode === 'create'
  const isEdit   = state.mode === 'edit'
  const isPwd    = state.mode === 'password'
  const isSelf   = !!me && state.user?.id === me.id

  const [username, setUsername] = useState(isEdit ? (state.user?.username ?? '') : '')
  const [email, setEmail]     = useState(state.user?.email ?? '')
  const [role, setRole]       = useState(state.user?.role ?? 'viewer')
  const [active, setActive]   = useState(state.user?.is_active ?? true)
  const [password, setPassword] = useState('')
  const [currentPwd, setCurrentPwd] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    try {
      if (isCreate) {
        await api.post('/users', { username, email, password, role })
        onSuccess('User created')
      } else if (isEdit) {
        await api.patch(`/users/${state.user!.id}`, { username: username || undefined, role, is_active: active })
        onSuccess('User updated')
      } else if (isPwd) {
        await api.post(`/users/${state.user!.id}/change-password`, {
          ...(isSelf && !me?.role.includes('admin') ? { current_password: currentPwd } : {}),
          new_password: password,
        })
        onSuccess('Password changed')
      }
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Operation failed'
      onError(msg)
    } finally {
      setLoading(false)
    }
  }

  const title = isCreate ? 'Create User' : isEdit ? 'Edit User' : 'Change Password'

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
          <h2 className="text-[14px] font-semibold text-gray-100">{title}</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200 transition-colors">
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {isCreate && (
            <>
              <div>
                <Label>Username</Label>
                <Input type="text" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus autoComplete="off" />
              </div>
              <div>
                <Label>Email</Label>
                <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="off" />
              </div>
              <div>
                <Label>Role</Label>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                  className="w-full bg-gray-900 border border-gray-700 text-gray-100 rounded-lg px-3 py-2 text-[13px] focus:outline-none focus:ring-2 focus:ring-brand-500/40"
                >
                  <option value="viewer">Viewer</option>
                  <option value="operator">Operator</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
            </>
          )}

          {isEdit && (
            <>
              <div className="text-[12px] text-gray-400">Editing: <span className="text-gray-200">{state.user?.username ?? state.user?.email}</span></div>
              <div>
                <Label>Username</Label>
                <Input type="text" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
              </div>
              <div>
                <Label>Role</Label>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                  className="w-full bg-gray-900 border border-gray-700 text-gray-100 rounded-lg px-3 py-2 text-[13px] focus:outline-none focus:ring-2 focus:ring-brand-500/40"
                >
                  <option value="viewer">Viewer</option>
                  <option value="operator">Operator</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
              <div>
                <Label>Status</Label>
                <label className="flex items-center gap-2 cursor-pointer mt-1">
                  <input
                    type="checkbox"
                    checked={active}
                    onChange={(e) => setActive(e.target.checked)}
                    className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-brand-500 focus:ring-brand-500/30"
                  />
                  <span className="text-[13px] text-gray-300">Active</span>
                </label>
              </div>
            </>
          )}

          {isPwd && (
            <>
              <div className="text-[12px] text-gray-400">Changing password for: <span className="text-gray-200">{state.user?.username ?? state.user?.email}</span></div>
              {isSelf && (
                <div>
                  <Label>Current password</Label>
                  <div className="relative">
                    <Input
                      type={showPwd ? 'text' : 'password'}
                      value={currentPwd}
                      onChange={(e) => setCurrentPwd(e.target.value)}
                      required
                      autoComplete="current-password"
                      className="pr-9"
                    />
                    <button type="button" onClick={() => setShowPwd((v) => !v)}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300">
                      {showPwd ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>
              )}
            </>
          )}

          {(isCreate || isPwd) && (
            <div>
              <Label>{isCreate ? 'Password' : 'New password'} <span className="text-gray-600">(min 8 characters)</span></Label>
              <div className="relative">
                <Input
                  type={showPwd ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={8}
                  autoComplete="new-password"
                  className="pr-9"
                />
                <button type="button" onClick={() => setShowPwd((v) => !v)}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300">
                  {showPwd ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Btn type="button" variant="ghost" onClick={onClose}>Cancel</Btn>
            <Btn type="submit" loading={loading}>
              {isCreate ? 'Create' : isEdit ? 'Save' : 'Change Password'}
            </Btn>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Schedules Tab ──────────────────────────────────────────────────────────

interface Schedule {
  id: string
  stack_name: string
  cron_expression: string
  is_active: boolean
  retention_manual_count: number
  retention_daily_days: number
  retention_weekly_weeks: number
  updated_at: string
}

function SchedulesTab() {
  const qc = useQueryClient()
  const { data: schedules = [], isLoading } = useQuery<Schedule[]>({
    queryKey: ['schedules'],
    queryFn: () => api.get('/stacks/schedules').then((r) => r.data),
  })
  const [editing, setEditing] = useState<Schedule | null>(null)
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null)

  const showToast = (msg: string, type: 'success' | 'error') => {
    setToast({ msg, type }); setTimeout(() => setToast(null), 3500)
  }

  const toggleActive = useMutation({
    mutationFn: (s: Schedule) =>
      api.put(`/stacks/${s.stack_name}/schedule`, {
        cron_expression: s.cron_expression,
        is_active: !s.is_active,
        retention_manual_count: s.retention_manual_count,
        retention_daily_days: s.retention_daily_days,
        retention_weekly_weeks: s.retention_weekly_weeks,
      }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['schedules'] }) },
    onError: () => showToast('Failed to toggle schedule', 'error'),
  })

  const deleteSchedule = useMutation({
    mutationFn: (stackName: string) => api.delete(`/stacks/${stackName}/schedule`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['schedules'] }); showToast('Schedule removed', 'success') },
    onError: () => showToast('Failed to remove schedule', 'error'),
  })

  if (isLoading) return <div className="text-gray-500 text-[13px]">Loading...</div>

  if (schedules.length === 0) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-500 text-[13px]">No schedules configured.</p>
        <p className="text-gray-600 text-[12px] mt-1">
          Schedules can be set per-stack from the Dashboard using the calendar icon on each stack card.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {toast && <Toast message={toast.msg} type={toast.type} />}
      {editing && (
        <ScheduleEditModal
          schedule={editing}
          onClose={() => setEditing(null)}
          onSuccess={() => { qc.invalidateQueries({ queryKey: ['schedules'] }); setEditing(null); showToast('Schedule updated', 'success') }}
          onError={(m) => showToast(m, 'error')}
        />
      )}

      <div className="rounded-xl border border-gray-800 overflow-hidden">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900/60">
              <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Stack</th>
              <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Cron</th>
              <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Retention</th>
              <th className="text-left px-4 py-2.5 text-gray-500 font-medium">Status</th>
              <th className="px-4 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {schedules.map((s) => (
              <tr key={s.id} className="border-b border-gray-800/60 hover:bg-gray-900/40">
                <td className="px-4 py-3 text-gray-200 font-medium">{s.stack_name}</td>
                <td className="px-4 py-3 font-mono text-brand-400">{s.cron_expression}</td>
                <td className="px-4 py-3 text-gray-400">
                  {s.retention_manual_count}m / {s.retention_daily_days}d / {s.retention_weekly_weeks}w
                </td>
                <td className="px-4 py-3">
                  <span className={clsx(
                    'px-2 py-0.5 rounded text-[10px] font-medium border',
                    s.is_active
                      ? 'bg-emerald-900/30 text-emerald-400 border-emerald-800'
                      : 'bg-gray-800 text-gray-500 border-gray-700',
                  )}>
                    {s.is_active ? 'active' : 'paused'}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-1 justify-end">
                    <button onClick={() => toggleActive.mutate(s)}
                      title={s.is_active ? 'Pause' : 'Resume'}
                      className="p-1.5 text-gray-500 hover:text-gray-200 rounded-lg hover:bg-gray-700/60">
                      {s.is_active ? <Shield size={12} /> : <Check size={12} />}
                    </button>
                    <button onClick={() => setEditing(s)}
                      className="p-1.5 text-gray-500 hover:text-gray-200 rounded-lg hover:bg-gray-700/60">
                      <Pencil size={12} />
                    </button>
                    <button
                      onClick={() => { if (confirm(`Remove schedule for ${s.stack_name}?`)) deleteSchedule.mutate(s.stack_name) }}
                      className="p-1.5 text-gray-500 hover:text-red-400 rounded-lg hover:bg-red-900/30">
                      <Trash2 size={12} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ScheduleEditModal({
  schedule, onClose, onSuccess, onError,
}: {
  schedule: Schedule
  onClose: () => void
  onSuccess: () => void
  onError: (msg: string) => void
}) {
  const [cron, setCron] = useState(schedule.cron_expression)
  const [active, setActive] = useState(schedule.is_active)
  const [retManual, setRetManual] = useState(schedule.retention_manual_count)
  const [retDaily, setRetDaily] = useState(schedule.retention_daily_days)
  const [retWeekly, setRetWeekly] = useState(schedule.retention_weekly_weeks)
  const [loading, setLoading] = useState(false)

  const presets = [
    { label: 'Every hour', value: '0 * * * *' },
    { label: 'Daily 2 AM', value: '0 2 * * *' },
    { label: 'Weekly (Sun 2 AM)', value: '0 2 * * 0' },
    { label: 'Every 6 h', value: '0 */6 * * *' },
  ]

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    try {
      await api.put(`/stacks/${schedule.stack_name}/schedule`, {
        cron_expression: cron,
        is_active: active,
        retention_manual_count: retManual,
        retention_daily_days: retDaily,
        retention_weekly_weeks: retWeekly,
      })
      onSuccess()
    } catch {
      onError('Failed to save schedule')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
          <h2 className="text-[14px] font-semibold text-gray-100">Edit Schedule — <span className="text-brand-400">{schedule.stack_name}</span></h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200"><X size={16} /></button>
        </div>
        <form onSubmit={handleSave} className="p-5 space-y-4">
          <div>
            <Label>Cron expression <span className="text-gray-600">(5-field: min hour day month weekday)</span></Label>
            <Input value={cron} onChange={(e) => setCron(e.target.value)} required placeholder="0 2 * * *" className="font-mono" />
            <div className="flex flex-wrap gap-1.5 mt-2">
              {presets.map((p) => (
                <button key={p.value} type="button" onClick={() => setCron(p.value)}
                  className="px-2 py-0.5 rounded text-[11px] bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200 border border-gray-700 transition-colors">
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <Label>Status</Label>
            <label className="flex items-center gap-2 cursor-pointer mt-1">
              <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)}
                className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-brand-500 focus:ring-brand-500/30" />
              <span className="text-[13px] text-gray-300">Active</span>
            </label>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label>Manual keep</Label>
              <Input type="number" min={1} value={retManual} onChange={(e) => setRetManual(Number(e.target.value))} />
            </div>
            <div>
              <Label>Daily (days)</Label>
              <Input type="number" min={0} value={retDaily} onChange={(e) => setRetDaily(Number(e.target.value))} />
            </div>
            <div>
              <Label>Weekly (weeks)</Label>
              <Input type="number" min={0} value={retWeekly} onChange={(e) => setRetWeekly(Number(e.target.value))} />
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Btn type="button" variant="ghost" onClick={onClose}>Cancel</Btn>
            <Btn type="submit" loading={loading}>Save Schedule</Btn>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Main Settings Page ─────────────────────────────────────────────────────

type Tab = 'general' | 'notifications' | 'users' | 'schedules'

const TABS: { id: Tab; label: string; Icon: React.ElementType }[] = [
  { id: 'general',       label: 'General',       Icon: Settings },
  { id: 'notifications', label: 'Notifications', Icon: Bell },
  { id: 'users',         label: 'Users',         Icon: Users },
  { id: 'schedules',     label: 'Schedules',     Icon: Shield },
]

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>('general')
  const { data, isLoading, isError } = useQuery<SettingsResponse>({
    queryKey: ['settings'],
    queryFn: () => api.get('/settings').then((r) => r.data),
  })

  // Suppress unused import warning for useCallback
  useCallback(() => {}, [])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500 gap-2 text-[13px]">
        <Loader2 size={16} className="animate-spin" /> Loading settings…
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="flex items-center justify-center h-64 text-red-400 gap-2 text-[13px]">
        <AlertCircle size={16} /> Failed to load settings.
      </div>
    )
  }

  return (
    <div>
      {/* Page header */}
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-100">Settings</h1>
        <p className="text-[13px] text-gray-500 mt-0.5">Manage application configuration and users</p>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-800 mb-6">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={clsx(
              'flex items-center gap-1.5 px-4 py-2.5 text-[13px] font-medium border-b-2 -mb-px transition-colors',
              tab === id
                ? 'border-brand-500 text-brand-400'
                : 'border-transparent text-gray-500 hover:text-gray-300',
            )}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'general'       && <GeneralTab data={data} />}
      {tab === 'notifications' && <NotificationsTab data={data} />}
      {tab === 'users'         && <UsersTab />}
      {tab === 'schedules'     && <SchedulesTab />}
    </div>
  )
}
