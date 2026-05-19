import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect, useRef, type ChangeEvent } from 'react'
import {
  Lock, Unlock, RotateCcw, CheckCircle, Clock,
  ArrowLeft, Radio, AlertCircle, Trash2, Camera, RefreshCw, Download, Upload,
} from 'lucide-react'
import clsx from 'clsx'
import { formatDistanceToNow } from 'date-fns'
import api from '../lib/api'
import type { SnapDockEvent, SnapshotResponse, StackResponse } from '../types'
import { useEventStream } from '../hooks/useEventStream'
import Modal from '../components/Modal'

/** Parse a datetime string that may lack a timezone suffix (backend returns naive UTC). */
const parseUTC = (s: string) => new Date(s.endsWith('Z') || s.includes('+') ? s : s + 'Z')

// ── Status badge / health colours ───────────────────────────────────────────
const STATE_BADGE: Record<string, string> = {
  CLEAN:    'bg-green-500/10 text-green-400 ring-1 ring-green-500/20',
  DEGRADED: 'bg-yellow-500/10 text-yellow-400 ring-1 ring-yellow-500/20',
  BROKEN:   'bg-red-500/10 text-red-400 ring-1 ring-red-500/20',
}

const HEALTH_BADGE: Record<string, string> = {
  CLEAN:    'bg-green-500/10 text-green-400 ring-1 ring-green-500/20',
  DEGRADED: 'bg-yellow-500/10 text-yellow-400 ring-1 ring-yellow-500/20',
  BROKEN:   'bg-red-500/10 text-red-400 ring-1 ring-red-500/20',
}

const CONTAINER_DOT: Record<string, string> = {
  running:    'bg-green-500',
  exited:     'bg-red-500',
  paused:     'bg-yellow-400',
  restarting: 'bg-blue-400 animate-pulse',
  dead:       'bg-red-700',
  created:    'bg-gray-500',
  removing:   'bg-orange-400',
}

// ── Stack status pane ─────────────────────────────────────────────────────────
function StackStatusPane({ stackName }: { stackName: string }) {
  const { data: stack, isLoading, isError } = useQuery<StackResponse>({
    queryKey: ['stack-live', stackName],
    queryFn: () => api.get(`/stacks/${stackName}`).then((r) => r.data),
    refetchInterval: 15_000,
    retry: false,
  })

  if (isLoading) {
    return (
      <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-4 animate-pulse">
        <div className="h-2.5 bg-gray-800 rounded w-24 mb-3" />
        <div className="space-y-2">
          <div className="h-2.5 bg-gray-800 rounded w-48" />
          <div className="h-2.5 bg-gray-800 rounded w-40" />
        </div>
      </div>
    )
  }

  if (isError || !stack) {
    return (
      <div className="rounded-xl border border-gray-800 bg-gray-900/40 px-4 py-3 flex items-center gap-2.5 text-[12px] text-gray-500">
        <div className="w-1.5 h-1.5 rounded-full bg-gray-600 shrink-0" />
        Stack is not currently running
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/40 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-800/60">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-500">Live Status</span>
        <span className={clsx('text-[11px] font-medium px-2 py-0.5 rounded-full', HEALTH_BADGE[stack.health_state] ?? 'text-gray-400 bg-gray-800')}>
          {stack.health_state}
        </span>
      </div>
      <div className="divide-y divide-gray-800/30">
        {stack.containers.map((c) => (
          <div key={c.id} className="flex items-center gap-3 px-4 py-2">
            <div className={clsx('w-1.5 h-1.5 rounded-full shrink-0', CONTAINER_DOT[c.status] ?? 'bg-gray-500')} />
            <span className="text-[12px] text-gray-200 font-medium shrink-0 min-w-0 truncate max-w-[180px]">{c.name}</span>
            <span className="text-[11px] text-gray-600 font-mono truncate flex-1 min-w-0">{c.image}</span>
            <span className="text-[11px] text-gray-500 shrink-0 capitalize">{c.status}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Skeleton row ─────────────────────────────────────────────────────────────
function SkeletonRow() {
  return (
    <tr className="animate-pulse">
      {[120, 80, 60, 70, 50, 80, 40, 64].map((w, i) => (
        <td key={i} className="px-4 py-3">
          <div className={`h-3 bg-gray-800 rounded`} style={{ width: w }} />
        </td>
      ))}
    </tr>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────
export default function SnapshotHistoryPage() {
  const { stackName } = useParams<{ stackName: string }>()
  const qc = useQueryClient()
  const rawEvents = useEventStream(stackName)
  const [liveVisible, setLiveVisible] = useState(false)
  const liveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [showSnapshotModal, setShowSnapshotModal] = useState(false)
  const [snapLabel, setSnapLabel] = useState('')
  const [importing, setImporting] = useState(false)
  const [importProgress, setImportProgress] = useState(0)

  // Show live panel when events arrive; auto-hide 6 s after last event
  useEffect(() => {
    if (rawEvents.length === 0) return
    setLiveVisible(true)
    if (liveTimerRef.current) clearTimeout(liveTimerRef.current)
    liveTimerRef.current = setTimeout(() => setLiveVisible(false), 6000)
    return () => { if (liveTimerRef.current) clearTimeout(liveTimerRef.current) }
  }, [rawEvents.length])

  const events = rawEvents

  const { data: snapshots, isLoading, error } = useQuery<SnapshotResponse[]>({
    queryKey: ['snapshots', stackName],
    queryFn: () => api.get(`/stacks/${stackName}/snapshots`).then((r) => r.data),
    refetchInterval: 10_000,
  })

  const triggerSnap = useMutation({
    mutationFn: () => api.post(`/stacks/${stackName}/snapshots`, { label: snapLabel.trim() || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['snapshots', stackName] })
      setShowSnapshotModal(false)
      setSnapLabel('')
    },
  })

  const refresh = () => qc.invalidateQueries({ queryKey: ['snapshots', stackName] })

  const handleImport = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    e.target.value = ''
    setImporting(true)
    setImportProgress(0)
    try {
      const form = new FormData()
      form.append('file', f)
      await api.post(`/stacks/${stackName}/snapshots/import`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (ev) => {
          if (ev.total) setImportProgress(Math.round((ev.loaded / ev.total) * 100))
        },
      })
      refresh()
    } catch (err: any) {
      alert(err?.response?.data?.detail ?? 'Import failed')
    } finally {
      setImporting(false)
      setImportProgress(0)
    }
  }

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link
            to="/dashboard"
            className="p-1.5 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
          >
            <ArrowLeft size={16} />
          </Link>
          <div>
            <h1 className="text-lg font-semibold text-white">{stackName}</h1>
            <p className="text-[13px] text-gray-500">Snapshot history</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex flex-col items-end gap-1">
            <label
              className={clsx(
                'flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg transition-colors cursor-pointer',
                importing
                  ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
                  : 'bg-gray-800 hover:bg-gray-700 text-gray-300',
              )}
              title="Import a .tar.gz snapshot archive"
            >
              <Upload size={13} className={importing ? 'animate-pulse' : ''} />
              {importing ? `Uploading… ${importProgress}%` : 'Import'}
              <input
                type="file"
                accept=".tar.gz"
                className="hidden"
                disabled={importing}
                onChange={handleImport}
              />
            </label>
            {importing && (
              <div className="h-0.5 w-full bg-gray-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-brand-600 transition-all duration-150"
                  style={{ width: `${importProgress}%` }}
                />
              </div>
            )}
          </div>
          <button
            onClick={() => setShowSnapshotModal(true)}
            className="flex items-center gap-2 px-3 py-1.5 text-sm bg-brand-600 hover:bg-brand-700 text-white rounded-lg transition-colors"
          >
            <Camera size={13} />
            Take Snapshot
          </button>
        </div>
      </div>

      {/* Live stack status */}
      <StackStatusPane stackName={stackName!} />

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 text-red-400 bg-red-950/20 border border-red-900/50 rounded-xl p-3 text-sm">
          <AlertCircle size={15} />
          Failed to load snapshots
        </div>
      )}

      {/* Live events — auto-hides 6 s after last event */}
      {liveVisible && events.length > 0 && (
        <div className="bg-gray-900/60 border border-gray-800 rounded-xl overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-800/60">
            <Radio size={10} className="text-green-400 animate-pulse" />
            <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-500">Live</span>
          </div>
          <div className="max-h-24 overflow-y-auto divide-y divide-gray-800/40 no-scrollbar">
            {events.slice(0, 8).map((e, i) => {
              const isDone  = e.event_type === 'snapshot.complete' || e.event_type === 'restore.complete'
              const isError = e.event_type === 'snapshot.error'   || e.event_type === 'restore.error'
              return (
                <div key={i} className={clsx(
                  'flex items-center gap-3 px-4 py-1.5 text-xs',
                  isDone ? 'text-green-400' : isError ? 'text-red-400' : '',
                )}>
                  <span className="text-gray-600 tabular-nums shrink-0">{(e.timestamp ?? '').slice(11, 19)}</span>
                  {isDone && <CheckCircle size={10} className="shrink-0" />}
                  {isError && <AlertCircle size={10} className="shrink-0" />}
                  <span className="text-gray-300 truncate">{e.message}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Table */}
      <div className="rounded-xl border border-gray-800 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-900/80">
              <tr>
                {['ID', 'Age', 'Trigger', 'State', 'Size', 'Label', 'Flags', 'Actions'].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-widest text-gray-500"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {isLoading
                ? [0, 1, 2].map((i) => <SkeletonRow key={i} />)
                : (snapshots ?? []).length === 0
                  ? (
                    <tr>
                      <td colSpan={8} className="text-center text-gray-600 text-sm py-12">
                        No snapshots yet
                      </td>
                    </tr>
                  )
                  : (snapshots ?? []).map((snap) => (
                    <SnapshotRow
                      key={snap.id}
                      snap={snap}
                      stackName={stackName!}
                      onRefresh={refresh}
                  events={events}
                    />
                  ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Take snapshot modal */}
      {showSnapshotModal && (
        <Modal
          title={`Snapshot · ${stackName}`}
          onClose={() => setShowSnapshotModal(false)}
          footer={
            <>
              <button
                onClick={() => setShowSnapshotModal(false)}
                className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => triggerSnap.mutate()}
                disabled={triggerSnap.isPending}
                className="px-4 py-2 text-sm bg-brand-600 hover:bg-brand-700 text-white rounded-lg transition-colors flex items-center gap-2 disabled:opacity-60"
              >
                {triggerSnap.isPending ? <RefreshCw size={13} className="animate-spin" /> : <Camera size={13} />}
                Take Snapshot
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <p className="text-[13px] text-gray-400 leading-relaxed">
              Containers will be briefly stopped, snapshotted (volumes + images), then restarted. Typically takes 10–60 seconds.
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
          </div>
        </Modal>
      )}
    </div>
  )
}

// ── Row ───────────────────────────────────────────────────────────────────────
function SnapshotRow({
  snap, stackName, onRefresh, events,
}: {
  snap: SnapshotResponse
  stackName: string
  onRefresh: () => void
  events: SnapDockEvent[]
}) {
  const restoreEvents = events.filter(
    (e) => e.snapshot_id === snap.id && e.event_type.startsWith('restore.'),
  )
  // Restore is two-step: first call returns a confirmation request, second call confirms
  const [restoreConfirm, setRestoreConfirm] = useState<{
    message: string
    data_loss_window: string
  } | null>(null)
  const [restoring, setRestoring] = useState(false)
  const logEndRef = useRef<HTMLDivElement>(null)

  // events are newest-first; check index 0 for completion
  const restoreDone = restoring && (
    restoreEvents[0]?.event_type === 'restore.complete' ||
    restoreEvents[0]?.event_type === 'restore.error'
  )

  // Auto-scroll restore log to bottom
  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [restoreEvents.length])

  // Close panel after completion
  useEffect(() => {
    if (!restoreDone) return
    const t = setTimeout(() => { setRestoring(false); onRefresh() }, 2000)
    return () => clearTimeout(t)
  }, [restoreDone])

  const restoreProbe = useMutation({
    mutationFn: () =>
      api.post(`/stacks/${stackName}/snapshots/${snap.id}/restore`, {
        confirmed: false,
        dry_run: false,
      }),
    onSuccess: (res) => {
      if (res.data?.requires_confirmation) {
        setRestoreConfirm({ message: res.data.message, data_loss_window: res.data.data_loss_window })
      } else {
        onRefresh()
      }
    },
  })

  const restoreConfirmed = useMutation({
    mutationFn: () =>
      api.post(`/stacks/${stackName}/snapshots/${snap.id}/restore`, {
        confirmed: true,
        dry_run: false,
      }),
    onSuccess: () => {
      setRestoreConfirm(null)
      setRestoring(true)
    },
  })

  const toggleLock = useMutation({
    mutationFn: () =>
      api.patch(`/stacks/${stackName}/snapshots/${snap.id}/lock`, { locked: !snap.locked }),
    onSuccess: onRefresh,
  })

  const deleteSnap = useMutation({
    mutationFn: () => api.delete(`/stacks/${stackName}/snapshots/${snap.id}`),
    onSuccess: onRefresh,
  })

  const [exporting, setExporting] = useState(false)
  const handleExport = async () => {
    setExporting(true)
    try {
      const res = await api.get(
        `/stacks/${stackName}/snapshots/${snap.id}/export`,
        { responseType: 'blob' },
      )
      const url = URL.createObjectURL(res.data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${snap.id}.tar.gz`
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setExporting(false)
    }
  }

  return (
    <>
      <tr className="hover:bg-gray-800/30 transition-colors group">
        {/* ID */}
        <td className="px-4 py-2.5 font-mono text-[11px] text-gray-400">
          <Link
            to={`/stacks/${stackName}/snapshots/${snap.id}`}
            className="hover:text-brand-400 transition-colors"
          >
            {snap.id}
          </Link>
        </td>
        {/* Age */}
        <td className="px-4 py-2.5 text-[12px] text-gray-400 whitespace-nowrap">
          {snap.generated_at
            ? formatDistanceToNow(parseUTC(snap.generated_at), { addSuffix: true })
            : '—'}
        </td>
        {/* Trigger */}
        <td className="px-4 py-2.5">
          <span className="text-[11px] bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">
            {snap.trigger_type}
          </span>
        </td>
        {/* State */}
        <td className="px-4 py-2.5">
          <span className={clsx(
            'text-[11px] font-medium px-2 py-0.5 rounded-full',
            STATE_BADGE[snap.stack_state] ?? 'text-gray-400',
          )}>
            {snap.stack_state}
          </span>
        </td>
        {/* Size */}
        <td className="px-4 py-2.5 text-[12px] text-gray-500 whitespace-nowrap">
          {snap.size_bytes ? `${(snap.size_bytes / 1048576).toFixed(1)} MB` : '—'}
        </td>
        {/* Label */}
        <td className="px-4 py-2.5 text-[12px] text-gray-500 max-w-[120px] truncate">
          {snap.label || <span className="text-gray-700">—</span>}
        </td>
        {/* Flags */}
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-1.5">
            {snap.complete
              ? <CheckCircle size={12} className="text-green-500" aria-label="Complete" />
              : <Clock size={12} className="text-yellow-500" aria-label="In progress" />}
            {snap.locked && <Lock size={12} className="text-orange-400" aria-label="Locked" />}
            {snap.verified && <CheckCircle size={12} className="text-blue-400" aria-label="Verified" />}
          </div>
        </td>
        {/* Actions */}
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {/* Lock / Unlock */}
            <button
              onClick={() => toggleLock.mutate()}
              title={snap.locked ? 'Unlock' : 'Lock'}
              className="p-1.5 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 transition-colors"
            >
              {snap.locked ? <Unlock size={11} /> : <Lock size={11} />}
            </button>
            {/* Restore */}
            <button
              onClick={() => restoreProbe.mutate()}
              disabled={restoreProbe.isPending || restoreConfirmed.isPending || restoring}
              title={restoring ? 'Restore in progress…' : 'Restore this snapshot'}
              className="p-1.5 rounded-md bg-gray-800 hover:bg-amber-900/60 text-gray-400 hover:text-amber-300 transition-colors disabled:opacity-50"
            >
              <RotateCcw size={11} className={(restoreProbe.isPending || restoreConfirmed.isPending || restoring) ? 'animate-spin' : ''} />
            </button>
            {/* Export */}
            <button
              onClick={handleExport}
              disabled={exporting}
              title="Export snapshot archive (.tar.gz)"
              className="p-1.5 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50"
            >
              <Download size={11} className={exporting ? 'animate-pulse' : ''} />
            </button>
            {/* Delete */}
            <button
              onClick={() => {
                if (snap.locked) return
                if (confirm(`Delete snapshot ${snap.id}?\nThis permanently removes the backup data and cannot be undone.`)) {
                  deleteSnap.mutate()
                }
              }}
              disabled={snap.locked || deleteSnap.isPending}
              title={snap.locked ? 'Unlock before deleting' : 'Delete snapshot'}
              className="p-1.5 rounded-md bg-gray-800 hover:bg-red-900/70 text-gray-400 hover:text-red-400 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <Trash2 size={11} className={deleteSnap.isPending ? 'animate-pulse' : ''} />
            </button>
          </div>
        </td>
      </tr>

      {/* Restore confirmation modal */}
      {restoring && (
        <tr>
          <td colSpan={8} className="px-4 py-3 bg-gray-950/60 border-t border-gray-800">
            <div className="space-y-2">
              <div className={clsx(
                'flex items-center gap-2 text-[11px] font-semibold uppercase tracking-widest',
                restoreDone
                  ? restoreEvents[0]?.event_type === 'restore.error' ? 'text-red-400' : 'text-green-400'
                  : 'text-amber-400',
              )}>
                {restoreDone
                  ? restoreEvents[0]?.event_type === 'restore.error'
                    ? <AlertCircle size={11} />
                    : <CheckCircle size={11} />
                  : <RefreshCw size={11} className="animate-spin" />}
                {restoreDone
                  ? restoreEvents[0]?.event_type === 'restore.error' ? 'Restore failed' : 'Restore complete'
                  : `Restoring — ${snap.id}`}
              </div>
              <div className="max-h-40 overflow-y-auto space-y-0.5 font-mono text-[11px]">
                {[...restoreEvents].reverse().map((e, i) => {
                  const isError = e.event_type === 'restore.error' || e.status === 'error'
                  const isDone  = e.event_type === 'restore.complete'
                  return (
                    <div key={i} className={clsx(
                      'flex items-start gap-2 px-2 py-0.5 rounded',
                      isError ? 'text-red-400 bg-red-950/20' :
                      isDone  ? 'text-green-400 bg-green-950/20' :
                                'text-gray-400',
                    )}>
                      <span className="shrink-0 tabular-nums text-gray-600">{(e.timestamp ?? '').slice(11, 19)}</span>
                      <span className="break-all">{e.message}</span>
                    </div>
                  )
                })}
                <div ref={logEndRef} />
              </div>
            </div>
          </td>
        </tr>
      )}

      {restoreConfirm && (
        <tr>
          <td colSpan={8} className="p-0">
            <Modal
              title={`Restore · ${snap.id}`}
              onClose={() => setRestoreConfirm(null)}
              footer={
                <>
                  <button
                    onClick={() => setRestoreConfirm(null)}
                    className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => restoreConfirmed.mutate()}
                    disabled={restoreConfirmed.isPending}
                    className="px-4 py-2 text-sm bg-red-700 hover:bg-red-600 text-white rounded-lg transition-colors flex items-center gap-2 disabled:opacity-60"
                  >
                    {restoreConfirmed.isPending ? <RefreshCw size={13} className="animate-spin" /> : <RotateCcw size={13} />}
                    Confirm Restore
                  </button>
                </>
              }
            >
              <div className="space-y-3 text-[13px]">
                <div className="flex items-start gap-2 p-3 bg-amber-950/30 border border-amber-900/50 rounded-lg">
                  <AlertCircle size={15} className="text-amber-400 mt-0.5 shrink-0" />
                  <p className="text-amber-300 leading-relaxed">{restoreConfirm.message}</p>
                </div>
                <p className="text-gray-500">
                  Data loss window: <span className="text-gray-300 font-medium">{restoreConfirm.data_loss_window}</span>
                </p>
              </div>
            </Modal>
          </td>
        </tr>
      )}
    </>
  )
}

