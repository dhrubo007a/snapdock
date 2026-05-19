import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Camera, RefreshCw, Zap, Server, Shield,
  Clock, Layers, AlertCircle, Radio, CalendarDays, X, Loader2,
} from 'lucide-react'
import clsx from 'clsx'
import api from '../lib/api'
import type { StackResponse } from '../types'
import { useEventStream } from '../hooks/useEventStream'
import Modal from '../components/Modal'

// ── Skeleton ─────────────────────────────────────────────────────────────────
function CardSkeleton() {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3 animate-pulse">
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <div className="h-4 w-32 bg-gray-800 rounded" />
          <div className="h-3 w-20 bg-gray-800 rounded" />
        </div>
        <div className="w-7 h-7 bg-gray-800 rounded-lg" />
      </div>
      <div className="h-3 w-24 bg-gray-800 rounded" />
    </div>
  )
}

function StatSkeleton() {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 flex items-center gap-3 animate-pulse">
      <div className="w-8 h-8 bg-gray-800 rounded-lg shrink-0" />
      <div className="space-y-1.5">
        <div className="h-5 w-8 bg-gray-800 rounded" />
        <div className="h-3 w-16 bg-gray-800 rounded" />
      </div>
    </div>
  )
}

// ── State colours ─────────────────────────────────────────────────────────────
const STATE = {
  CLEAN:    { dot: 'bg-green-400',  ring: 'ring-green-500/20',  bg: 'bg-green-500/10',  text: 'text-green-400'  },
  DEGRADED: { dot: 'bg-yellow-400', ring: 'ring-yellow-500/20', bg: 'bg-yellow-500/10', text: 'text-yellow-400' },
  BROKEN:   { dot: 'bg-red-400',    ring: 'ring-red-500/20',    bg: 'bg-red-500/10',    text: 'text-red-400'    },
} as const

// ── Page ─────────────────────────────────────────────────────────────────────
export default function DashboardPage() {
  const { data: stacks, isLoading, error } = useQuery<StackResponse[]>({
    queryKey: ['stacks'],
    queryFn: () => api.get('/stacks').then((r) => r.data),
    refetchInterval: 15_000,
  })

  const events = useEventStream()

  const clean    = stacks?.filter((s) => s.health_state === 'CLEAN')    ?? []
  const degraded = stacks?.filter((s) => s.health_state === 'DEGRADED') ?? []
  const broken   = stacks?.filter((s) => s.health_state === 'BROKEN')   ?? []
  const issues   = degraded.length + broken.length
  const scheduled = stacks?.filter((s) => s.has_schedule).length ?? 0

  const lanes = [
    { label: 'Healthy',  stacks: clean,    border: 'border-green-500',  badge: 'bg-green-500/10 text-green-400'  },
    { label: 'Degraded', stacks: degraded, border: 'border-yellow-400', badge: 'bg-yellow-500/10 text-yellow-400' },
    { label: 'Broken',   stacks: broken,   border: 'border-red-500',    badge: 'bg-red-500/10 text-red-400'      },
  ]

  if (error) {
    return (
      <div className="flex items-center gap-3 text-red-400 bg-red-950/20 border border-red-900/50 rounded-xl p-4 text-sm">
        <AlertCircle size={16} className="shrink-0" />
        Failed to load stacks — is the SnapDock daemon reachable?
      </div>
    )
  }

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-lg font-semibold text-white">Dashboard</h1>
        <p className="text-[13px] text-gray-500 mt-0.5">Monitor and snapshot your Docker stacks</p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {isLoading ? (
          Array.from({ length: 4 }).map((_, i) => <StatSkeleton key={i} />)
        ) : (
          <>
            <StatCard Icon={Layers}       label="Total Stacks" value={stacks?.length ?? 0} />
            <StatCard Icon={Shield}       label="Healthy"      value={clean.length}     color="text-green-400" />
            <StatCard Icon={AlertCircle}  label="Issues"       value={issues}            color={issues > 0 ? 'text-yellow-400' : 'text-gray-400'} />
            <StatCard Icon={Zap}          label="Scheduled"    value={scheduled}         color="text-brand-400" />
          </>
        )}
      </div>

      {/* Live event ticker */}
      {events.length > 0 && (
        <div className="bg-gray-900/60 border border-gray-800 rounded-xl overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-800/60">
            <Radio size={11} className="text-green-400 animate-pulse" />
            <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-500">Live</span>
          </div>
          <div className="max-h-24 overflow-y-auto divide-y divide-gray-800/40 no-scrollbar">
            {events.slice(0, 8).map((e, i) => (
              <div key={i} className="flex items-center gap-3 px-4 py-1.5 text-xs">
                <span className="text-gray-600 tabular-nums shrink-0">{(e.timestamp ?? '').slice(11, 19)}</span>
                <span className={clsx(
                  'shrink-0 font-mono text-[10px] px-1.5 py-0.5 rounded',
                  e.status === 'error'   ? 'bg-red-900/40 text-red-400' :
                  e.status === 'warning' ? 'bg-yellow-900/40 text-yellow-400' :
                                           'bg-gray-800 text-gray-400',
                )}>
                  {e.event_type}
                </span>
                <span className="text-gray-300 truncate">{e.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Rows per health grade */}
      <div className="space-y-6">
        {lanes.map(({ label, stacks: lane, border, badge }) => (
          <div key={label}>
            <div className="flex items-center gap-2 mb-3">
              <span className={clsx('h-3.5 w-0.5 rounded-full', border.replace('border-', 'bg-'))} />
              <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-500">{label}</span>
              <span className={clsx('ml-1 text-[11px] font-bold px-2 py-0.5 rounded-full', badge)}>
                {lane.length}
              </span>
            </div>

            {isLoading ? (
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
                {[0, 1, 2, 3].map((i) => <CardSkeleton key={i} />)}
              </div>
            ) : lane.length === 0 ? (
              <div className="text-center text-gray-700 text-xs py-6 border border-dashed border-gray-800/80 rounded-xl">
                None
              </div>
            ) : (
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
                {lane.map((stack) => <StackCard key={stack.name} stack={stack} />)}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── StatCard ──────────────────────────────────────────────────────────────────
function StatCard({
  Icon, label, value, color = 'text-gray-200',
}: {
  Icon: React.ElementType
  label: string
  value: number | string
  color?: string
}) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 flex items-center gap-3 hover:border-gray-700 transition-colors">
      <div className="w-8 h-8 rounded-lg bg-gray-800 flex items-center justify-center shrink-0">
        <Icon size={14} className="text-gray-400" />
      </div>
      <div>
        <p className={clsx('text-lg font-bold leading-none', color)}>{value}</p>
        <p className="text-[11px] text-gray-500 mt-0.5">{label}</p>
      </div>
    </div>
  )
}

// ── StackCard ─────────────────────────────────────────────────────────────────
function StackCard({ stack }: { stack: StackResponse }) {
  const qc = useQueryClient()
  const [showConfirm, setShowConfirm] = useState(false)
  const [showSchedule, setShowSchedule] = useState(false)
  const [snapLabel, setSnapLabel] = useState('')

  const triggerSnap = useMutation({
    mutationFn: () => api.post(`/stacks/${stack.name}/snapshots`, { label: snapLabel.trim() || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['stacks'] })
      setShowConfirm(false)
      setSnapLabel('')
    },
  })

  const sc = STATE[stack.health_state as keyof typeof STATE] ?? STATE.CLEAN

  return (
    <>
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 hover:border-gray-700/80 transition-all duration-150 group">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', sc.dot)} />
              <Link
                to={`/stacks/${stack.name}/snapshots`}
                className="text-[13px] font-semibold text-gray-200 hover:text-brand-400 truncate transition-colors"
              >
                {stack.name}
              </Link>
            </div>
            <p className="text-[11px] text-gray-600 mt-1 ml-3">
              {stack.type} · {stack.containers.length} container{stack.containers.length !== 1 ? 's' : ''}
            </p>
          </div>
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {!stack.snapshot_protected && (
              <button
                onClick={() => setShowSchedule(true)}
                title="Set snapshot schedule"
                className="shrink-0 p-1.5 rounded-lg bg-gray-800 hover:bg-brand-600/70 transition-all duration-150 text-gray-500 hover:text-white"
              >
                <CalendarDays size={12} />
              </button>
            )}
            <button
              onClick={() => { if (!stack.snapshot_protected) setShowConfirm(true) }}
              disabled={stack.snapshot_protected}
              title={stack.snapshot_protected ? 'SnapDock itself cannot be snapshotted' : 'Take snapshot'}
              className={clsx(
                'shrink-0 p-1.5 rounded-lg transition-all duration-150',
                stack.snapshot_protected
                  ? 'bg-gray-800/40 text-gray-700 cursor-not-allowed'
                  : 'bg-gray-800 hover:bg-brand-600 text-gray-500 hover:text-white',
              )}
            >
              <Camera size={12} />
            </button>
          </div>
        </div>

        <div className="mt-2.5 flex items-center gap-3 text-[11px] text-gray-600 ml-3">
          <span className="flex items-center gap-1">
            <Clock size={9} />
            {stack.last_snapshot_at
              ? new Date(stack.last_snapshot_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
              : 'never snapped'}
          </span>
          {stack.has_schedule && (
            <span className="flex items-center gap-1 text-brand-500/70">
              <Zap size={9} />scheduled
            </span>
          )}
        </div>
      </div>

      {/* Confirm snapshot modal */}
      {showConfirm && (
        <Modal
          title={`Snapshot · ${stack.name}`}
          onClose={() => setShowConfirm(false)}
          footer={
            <>
              <button
                onClick={() => setShowConfirm(false)}
                className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => triggerSnap.mutate()}
                disabled={triggerSnap.isPending}
                className="px-4 py-2 text-sm bg-brand-600 hover:bg-brand-700 text-white rounded-lg transition-colors flex items-center gap-2 disabled:opacity-60"
              >
                {triggerSnap.isPending
                  ? <RefreshCw size={13} className="animate-spin" />
                  : <Camera size={13} />}
                Take Snapshot
              </button>
            </>
          }
        >
          <div className="space-y-3 text-[13px]">
            <p className="text-gray-400 leading-relaxed">
              Containers will be briefly stopped, snapshotted, then restarted. The operation typically takes 5–30 seconds.
            </p>
            <div>
              <label className="block text-[11px] font-medium text-gray-500 mb-1">Label <span className="text-gray-700">(optional)</span></label>
              <input
                type="text"
                value={snapLabel}
                onChange={(e) => setSnapLabel(e.target.value)}
                placeholder="e.g. pre-deploy, before-migration"
                className="w-full bg-gray-900 border border-gray-700 text-gray-100 rounded-lg px-3 py-2 text-[13px] placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500/60"
              />
            </div>
            <div className="bg-gray-800/60 border border-gray-700/50 rounded-lg p-3 space-y-1.5">
              {stack.containers.map((c) => (
                <div key={c.id} className="flex items-center gap-2.5">
                  <Server size={11} className="text-gray-500 shrink-0" />
                  <span className="text-gray-300 font-mono text-xs truncate flex-1">{c.name}</span>
                  <span className={clsx(
                    'text-[10px] px-1.5 py-0.5 rounded-full',
                    c.status === 'running'
                      ? 'bg-green-500/10 text-green-400'
                      : 'bg-gray-700 text-gray-500',
                  )}>
                    {c.status}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </Modal>
      )}

      {/* Schedule modal */}
      {showSchedule && (
        <ScheduleModal
          stackName={stack.name}
          onClose={() => setShowSchedule(false)}
          onSaved={() => { setShowSchedule(false); qc.invalidateQueries({ queryKey: ['stacks'] }) }}
        />
      )}
    </>
  )
}

// ── ScheduleModal ─────────────────────────────────────────────────────────────
interface ScheduleData {
  cron_expression: string
  is_active: boolean
  retention_manual_count: number
  retention_daily_days: number
  retention_weekly_weeks: number
}

function ScheduleModal({
  stackName, onClose, onSaved,
}: {
  stackName: string
  onClose: () => void
  onSaved: () => void
}) {
  const [cron, setCron] = useState('0 2 * * *')
  const [active, setActive] = useState(true)
  const [retManual, setRetManual] = useState(5)
  const [retDaily, setRetDaily] = useState(7)
  const [retWeekly, setRetWeekly] = useState(4)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [hasSchedule, setHasSchedule] = useState(false)
  const qc = useQueryClient()

  // Load existing schedule
  useState(() => {
    setLoading(true)
    api.get(`/stacks/${stackName}/schedule`)
      .then((r) => {
        const s: ScheduleData = r.data
        setCron(s.cron_expression)
        setActive(s.is_active)
        setRetManual(s.retention_manual_count)
        setRetDaily(s.retention_daily_days)
        setRetWeekly(s.retention_weekly_weeks)
        setHasSchedule(true)
      })
      .catch(() => { /* no schedule yet */ })
      .finally(() => setLoading(false))
  })

  const presets = [
    { label: 'Every hour',     value: '0 * * * *'  },
    { label: 'Daily 2 AM',     value: '0 2 * * *'  },
    { label: 'Weekly Sun 2AM', value: '0 2 * * 0'  },
    { label: 'Every 6 h',      value: '0 */6 * * *' },
  ]

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      await api.put(`/stacks/${stackName}/schedule`, {
        cron_expression: cron, is_active: active,
        retention_manual_count: retManual,
        retention_daily_days: retDaily,
        retention_weekly_weeks: retWeekly,
      })
      qc.invalidateQueries({ queryKey: ['schedules'] })
      onSaved()
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (!confirm(`Remove schedule for ${stackName}?`)) return
    await api.delete(`/stacks/${stackName}/schedule`)
    qc.invalidateQueries({ queryKey: ['schedules'] })
    onSaved()
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
          <h2 className="text-[14px] font-semibold text-gray-100">
            Schedule — <span className="text-brand-400">{stackName}</span>
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200">
            <X size={16} />
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center h-32 text-gray-500 gap-2 text-[13px]">
            <Loader2 size={15} className="animate-spin" /> Loading…
          </div>
        ) : (
          <form onSubmit={handleSave} className="p-5 space-y-4">
            <div>
              <label className="block text-[12px] font-medium text-gray-400 mb-1">
                Cron expression <span className="text-gray-600">(5-field: min hour day month weekday)</span>
              </label>
              <input
                value={cron}
                onChange={(e) => setCron(e.target.value)}
                required
                placeholder="0 2 * * *"
                className="w-full bg-gray-900 border border-gray-700 text-gray-100 rounded-lg px-3 py-2 text-[13px] font-mono focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500/60"
              />
              <div className="flex flex-wrap gap-1.5 mt-2">
                {presets.map((p) => (
                  <button key={p.value} type="button" onClick={() => setCron(p.value)}
                    className="px-2 py-0.5 rounded text-[11px] bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200 border border-gray-700">
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)}
                className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-brand-500 focus:ring-brand-500/30" />
              <span className="text-[13px] text-gray-300">Active</span>
            </label>

            <div>
              <p className="text-[12px] font-medium text-gray-400 mb-2">Retention</p>
              <div className="grid grid-cols-3 gap-3">
                {([['retManual', retManual, setRetManual, 'Manual keep'],
                   ['retDaily',  retDaily,  setRetDaily,  'Daily (days)'],
                   ['retWeekly', retWeekly, setRetWeekly, 'Weekly (wks)']] as const).map(([, val, setter, lbl]) => (
                  <div key={lbl}>
                    <label className="block text-[11px] text-gray-500 mb-1">{lbl}</label>
                    <input type="number" min={0} value={val}
                      onChange={(e) => (setter as (n: number) => void)(Number(e.target.value))}
                      className="w-full bg-gray-900 border border-gray-700 text-gray-100 rounded-lg px-3 py-2 text-[13px] focus:outline-none focus:ring-2 focus:ring-brand-500/40" />
                  </div>
                ))}
              </div>
            </div>

            <div className="flex items-center justify-between pt-2">
              {hasSchedule ? (
                <button type="button" onClick={handleDelete}
                  className="text-[12px] text-red-500 hover:text-red-400 transition-colors">
                  Remove schedule
                </button>
              ) : <span />}
              <div className="flex gap-2">
                <button type="button" onClick={onClose}
                  className="px-3 py-1.5 rounded-lg bg-gray-800 text-gray-300 text-[12px] hover:bg-gray-700">
                  Cancel
                </button>
                <button type="submit" disabled={saving}
                  className="px-3 py-1.5 rounded-lg bg-brand-600 text-white text-[12px] hover:bg-brand-500 disabled:opacity-40 flex items-center gap-1.5">
                  {saving && <Loader2 size={11} className="animate-spin" />}
                  Save
                </button>
              </div>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

