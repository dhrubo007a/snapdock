import { useState, useMemo, useEffect, useRef, type ChangeEvent } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Upload, Search, ChevronDown, ChevronRight,
  Lock, Unlock, RotateCcw, Download, Trash2, AlertCircle,
  CheckCircle, RefreshCw, Radio, ExternalLink, FlaskConical,
} from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import clsx from 'clsx'
import api from '../lib/api'
import type { SnapshotResponse, SnapDockEvent } from '../types'
import { useEventStream } from '../hooks/useEventStream'
import Modal from '../components/Modal'

const parseUTC = (s: string) =>
  new Date(s.endsWith('Z') || s.includes('+') ? s : s + 'Z')

function fmtBytes(n: number | null) {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

const STATE_BADGE: Record<string, string> = {
  CLEAN:    'bg-green-500/10 text-green-400 ring-1 ring-green-500/20',
  DEGRADED: 'bg-yellow-500/10 text-yellow-400 ring-1 ring-yellow-500/20',
  BROKEN:   'bg-red-500/10 text-red-400 ring-1 ring-red-500/20',
}

// ── Skeleton ─────────────────────────────────────────────────────────────────
function SkeletonGroup() {
  return (
    <div className="rounded-xl border border-gray-800 overflow-hidden animate-pulse">
      <div className="px-4 py-3 bg-gray-900/60 flex items-center gap-3">
        <div className="h-3 w-28 bg-gray-800 rounded" />
        <div className="h-3 w-6 bg-gray-800 rounded" />
      </div>
      {[0, 1].map((i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-3 border-t border-gray-800/60">
          {[100, 60, 56, 64, 52, 40, 72].map((w, j) => (
            <div key={j} className="h-3 bg-gray-800 rounded" style={{ width: w }} />
          ))}
        </div>
      ))}
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function AllSnapshotsPage() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [importing, setImporting] = useState(false)
  const [importProgress, setImportProgress] = useState(0)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [liveVisible, setLiveVisible] = useState(false)
  const liveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Subscribe to ALL stacks via global event stream
  const events = useEventStream()

  useEffect(() => {
    const active = events.filter(
      (e) => e.event_type !== 'ping',
    )
    if (active.length === 0) return
    setLiveVisible(true)
    if (liveTimerRef.current) clearTimeout(liveTimerRef.current)
    liveTimerRef.current = setTimeout(() => setLiveVisible(false), 8000)
    return () => { if (liveTimerRef.current) clearTimeout(liveTimerRef.current) }
  }, [events.length])

  const { data: snapshots, isLoading, error } = useQuery<SnapshotResponse[]>({
    queryKey: ['all-snapshots'],
    queryFn: () => api.get('/snapshots').then((r) => r.data),
    refetchInterval: 10_000,
  })

  const refresh = () => qc.invalidateQueries({ queryKey: ['all-snapshots'] })

  // Group + filter
  const grouped = useMemo(() => {
    const q = search.toLowerCase()
    const filtered = (snapshots ?? []).filter(
      (s) =>
        !q ||
        s.stack_name.toLowerCase().includes(q) ||
        s.id.toLowerCase().includes(q) ||
        (s.label ?? '').toLowerCase().includes(q),
    )
    const map = new Map<string, SnapshotResponse[]>()
    for (const s of filtered) {
      if (!map.has(s.stack_name)) map.set(s.stack_name, [])
      map.get(s.stack_name)!.push(s)
    }
    // Sort stacks alphabetically
    return new Map([...map.entries()].sort(([a], [b]) => a.localeCompare(b)))
  }, [snapshots, search])

  const totalCount = snapshots?.length ?? 0

  const handleImport = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    e.target.value = ''
    setImporting(true)
    setImportProgress(0)
    try {
      const form = new FormData()
      form.append('file', f)
      await api.post('/snapshots/import', form, {
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

  const toggleCollapse = (stackName: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.has(stackName) ? next.delete(stackName) : next.add(stackName)
      return next
    })

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-lg font-semibold text-white">All Snapshots</h1>
            {totalCount > 0 && (
              <span className="text-[11px] bg-gray-800 text-gray-400 rounded-md px-1.5 py-0.5 font-medium">
                {totalCount}
              </span>
            )}
          </div>
          <p className="text-[13px] text-gray-500 mt-0.5">
            Every snapshot across all stacks — restore from any, even when the stack isn't running
          </p>
        </div>
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
            {importing ? `Uploading… ${importProgress}%` : 'Import snapshot'}
            <input
              type="file"
              accept=".tar.gz,.tgz"
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
      </div>

      {/* Search */}
      <div className="relative">
        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-600" />
        <input
          type="text"
          placeholder="Filter by stack, snapshot ID, or label…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full bg-gray-900 border border-gray-800 rounded-lg pl-8 pr-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-brand-500/40"
        />
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 text-red-400 bg-red-950/20 border border-red-900/50 rounded-xl p-3 text-sm">
          <AlertCircle size={15} />
          Failed to load snapshots
        </div>
      )}

      {/* Live events panel */}
      {liveVisible && events.length > 0 && (
        <div className="bg-gray-900/60 border border-gray-800 rounded-xl overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-800/60">
            <Radio size={10} className="text-green-400 animate-pulse" />
            <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-500">
              Live operations
            </span>
          </div>
          <div className="max-h-24 overflow-y-auto divide-y divide-gray-800/40 no-scrollbar">
            {events.slice(0, 10).map((e, i) => {
              const isDone  = e.event_type === 'snapshot.complete' || e.event_type === 'restore.complete'
              const isError = e.event_type === 'snapshot.error'   || e.event_type === 'restore.error'
              return (
                <div key={i} className={clsx(
                  'flex items-center gap-3 px-4 py-1.5 text-xs',
                  isDone ? 'text-green-400' : isError ? 'text-red-400' : 'text-gray-400',
                )}>
                  <span className="text-gray-600 tabular-nums shrink-0">{(e.timestamp ?? '').slice(11, 19)}</span>
                  {e.stack_name && (
                    <span className="text-gray-600 shrink-0">{e.stack_name}</span>
                  )}
                  {isDone && <CheckCircle size={10} className="shrink-0" />}
                  {isError && <AlertCircle size={10} className="shrink-0" />}
                  <span className="text-gray-300 truncate">{e.message}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Groups */}
      {isLoading ? (
        [0, 1, 2].map((i) => <SkeletonGroup key={i} />)
      ) : grouped.size === 0 ? (
        <div className="text-center text-gray-600 text-sm py-16 bg-gray-900/30 rounded-xl border border-gray-800/60">
          {search ? 'No snapshots match your search' : 'No snapshots yet'}
        </div>
      ) : (
        [...grouped.entries()].map(([stackName, snaps]) => (
          <StackGroup
            key={stackName}
            stackName={stackName}
            snaps={snaps}
            events={events}
            collapsed={collapsed.has(stackName)}
            onToggle={() => toggleCollapse(stackName)}
            onRefresh={refresh}
          />
        ))
      )}
    </div>
  )
}

// ── Stack group ───────────────────────────────────────────────────────────────
function StackGroup({
  stackName, snaps, events, collapsed, onToggle, onRefresh,
}: {
  stackName: string
  snaps: SnapshotResponse[]
  events: SnapDockEvent[]
  collapsed: boolean
  onToggle: () => void
  onRefresh: () => void
}) {
  const latestState = snaps[0]?.stack_state ?? ''
  return (
    <div className="rounded-xl border border-gray-800 overflow-hidden">
      {/* Group header */}
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-900/60 hover:bg-gray-800/60 transition-colors text-left"
      >
        <div className="flex items-center gap-2.5">
          {collapsed
            ? <ChevronRight size={13} className="text-gray-600" />
            : <ChevronDown size={13} className="text-gray-600" />}
          <span className="font-medium text-gray-100 text-[13px]">{stackName}</span>
          <span className="text-[11px] bg-gray-800/80 text-gray-500 rounded px-1.5 py-0.5">
            {snaps.length}
          </span>
          {latestState && (
            <span className={clsx(
              'text-[10px] font-medium rounded px-1.5 py-0.5',
              STATE_BADGE[latestState] ?? 'bg-gray-800 text-gray-500',
            )}>
              {latestState}
            </span>
          )}
        </div>
        <Link
          to={`/stacks/${stackName}/snapshots`}
          onClick={(e) => e.stopPropagation()}
          className="flex items-center gap-1 text-[11px] text-brand-400 hover:text-brand-300 transition-colors shrink-0"
        >
          View history
          <ExternalLink size={10} />
        </Link>
      </button>

      {/* Rows */}
      {!collapsed && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-t border-gray-800/60">
                {['ID', 'Age', 'State', 'Size', 'Trigger', 'Label', 'Flags', 'Actions'].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-2 text-left text-[10px] font-semibold uppercase tracking-widest text-gray-600"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/40">
              {snaps.map((snap) => (
                <SnapshotRow
                  key={snap.id}
                  snap={snap}
                  events={events.filter(
                    (e) => e.stack_name === stackName,
                  )}
                  onRefresh={onRefresh}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Snapshot row ──────────────────────────────────────────────────────────────
function SnapshotRow({
  snap, events, onRefresh,
}: {
  snap: SnapshotResponse
  events: SnapDockEvent[]
  onRefresh: () => void
}) {
  const restoreEvents = events.filter(
    (e) => e.snapshot_id === snap.id && e.event_type.startsWith('restore.'),
  )
  const [restoreConfirm, setRestoreConfirm] = useState<{
    message: string
    data_loss_window: string
  } | null>(null)
  const [restoring, setRestoring] = useState(false)
  const [isDryRunning, setIsDryRunning] = useState(false)
  const [exporting, setExporting] = useState(false)
  const logEndRef = useRef<HTMLDivElement>(null)

  const restoreDone = restoring && (
    restoreEvents[0]?.event_type === 'restore.complete' ||
    restoreEvents[0]?.event_type === 'restore.error'
  )

  const dryRunDone = isDryRunning && (
    restoreEvents[0]?.event_type === 'restore.complete' ||
    restoreEvents[0]?.event_type === 'restore.error'
  )

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [restoreEvents.length])

  useEffect(() => {
    if (!restoreDone) return
    const t = setTimeout(() => { setRestoring(false); onRefresh() }, 2000)
    return () => clearTimeout(t)
  }, [restoreDone])

  useEffect(() => {
    if (!dryRunDone) return
    const t = setTimeout(() => setIsDryRunning(false), 3000)
    return () => clearTimeout(t)
  }, [dryRunDone])

  const restoreProbe = useMutation({
    mutationFn: () =>
      api.post(`/stacks/${snap.stack_name}/snapshots/${snap.id}/restore`, {
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
      api.post(`/stacks/${snap.stack_name}/snapshots/${snap.id}/restore`, {
        confirmed: true,
        dry_run: false,
      }),
    onSuccess: () => {
      setRestoreConfirm(null)
      setRestoring(true)
    },
  })

  const dryRunMutation = useMutation({
    mutationFn: () =>
      api.post(`/stacks/${snap.stack_name}/snapshots/${snap.id}/restore`, {
        confirmed: false,
        dry_run: true,
      }),
    onSuccess: () => setIsDryRunning(true),
  })

  const toggleLock = useMutation({
    mutationFn: () =>
      api.patch(`/stacks/${snap.stack_name}/snapshots/${snap.id}/lock`, {
        locked: !snap.locked,
      }),
    onSuccess: onRefresh,
  })

  const deleteSnap = useMutation({
    mutationFn: () =>
      api.delete(`/stacks/${snap.stack_name}/snapshots/${snap.id}`),
    onSuccess: onRefresh,
  })

  const handleExport = async () => {
    setExporting(true)
    try {
      const res = await api.get(
        `/stacks/${snap.stack_name}/snapshots/${snap.id}/export`,
        { responseType: 'blob' },
      )
      const url = URL.createObjectURL(res.data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${snap.stack_name}_${snap.id}.tar.gz`
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setExporting(false)
    }
  }

  const shortId = snap.id.replace('snap_', '').slice(-8)

  return (
    <>
      <tr className={clsx(
        'hover:bg-gray-800/20 transition-colors group',
        restoring && 'bg-brand-950/10',
      )}>
        {/* ID */}
        <td className="px-4 py-2.5 font-mono text-[11px] text-gray-400">
          <Link
            to={`/stacks/${snap.stack_name}/snapshots/${snap.id}`}
            className="hover:text-brand-400 transition-colors"
          >
            {shortId}
          </Link>
        </td>

        {/* Age */}
        <td className="px-4 py-2.5 text-[12px] text-gray-500 whitespace-nowrap">
          {formatDistanceToNow(parseUTC(snap.generated_at), { addSuffix: true })}
        </td>

        {/* State */}
        <td className="px-4 py-2.5">
          {snap.stack_state ? (
            <span className={clsx(
              'text-[10px] font-medium rounded px-1.5 py-0.5',
              STATE_BADGE[snap.stack_state] ?? 'bg-gray-800 text-gray-500',
            )}>
              {snap.stack_state}
            </span>
          ) : <span className="text-gray-700">—</span>}
        </td>

        {/* Size */}
        <td className="px-4 py-2.5 text-[12px] text-gray-500 tabular-nums whitespace-nowrap">
          {fmtBytes(snap.size_bytes)}
        </td>

        {/* Trigger */}
        <td className="px-4 py-2.5 text-[12px] text-gray-600 capitalize">
          {snap.trigger_type ?? '—'}
        </td>

        {/* Label */}
        <td className="px-4 py-2.5 text-[12px] text-gray-500 max-w-[120px] truncate">
          {snap.label ?? <span className="text-gray-700">—</span>}
        </td>

        {/* Flags */}
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-1.5">
            {snap.locked && (
              <span title="Locked" className="text-amber-500"><Lock size={11} /></span>
            )}
            {snap.verified && (
              <span title="Verified" className="text-green-500"><CheckCircle size={11} /></span>
            )}
            {!snap.complete && (
              <span title="Incomplete" className="text-red-500"><AlertCircle size={11} /></span>
            )}
          </div>
        </td>

        {/* Actions */}
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {/* Restore */}
            <button
              onClick={() => restoreProbe.mutate()}
              disabled={restoreProbe.isPending || restoring || !snap.complete}
              title="Restore"
              className="p-1.5 rounded text-gray-500 hover:text-brand-400 hover:bg-brand-500/10 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              {restoreProbe.isPending || restoring
                ? <RefreshCw size={12} className="animate-spin" />
                : <RotateCcw size={12} />}
            </button>

            {/* Dry Run */}
            <button
              onClick={() => dryRunMutation.mutate()}
              disabled={dryRunMutation.isPending || isDryRunning || restoring || !snap.complete}
              title={isDryRunning ? 'Dry run in progress…' : 'Dry-run restore (no data overwrite)'}
              className="p-1.5 rounded text-gray-500 hover:text-blue-400 hover:bg-blue-500/10 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              {dryRunMutation.isPending || isDryRunning
                ? <RefreshCw size={12} className="animate-spin" />
                : <FlaskConical size={12} />}
            </button>

            {/* Export */}
            <button
              onClick={handleExport}
              disabled={exporting || !snap.complete}
              title="Export"
              className="p-1.5 rounded text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              {exporting
                ? <RefreshCw size={12} className="animate-spin" />
                : <Download size={12} />}
            </button>

            {/* Lock toggle */}
            <button
              onClick={() => toggleLock.mutate()}
              disabled={toggleLock.isPending}
              title={snap.locked ? 'Unlock' : 'Lock'}
              className="p-1.5 rounded text-gray-500 hover:text-amber-400 hover:bg-amber-500/10 transition-colors disabled:opacity-30"
            >
              {snap.locked ? <Unlock size={12} /> : <Lock size={12} />}
            </button>

            {/* Delete */}
            <button
              onClick={() => {
                if (snap.locked) return
                if (confirm(`Delete ${snap.id}? This cannot be undone.`)) deleteSnap.mutate()
              }}
              disabled={deleteSnap.isPending || snap.locked}
              title={snap.locked ? 'Unlock before deleting' : 'Delete'}
              className="p-1.5 rounded text-gray-500 hover:text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              {deleteSnap.isPending
                ? <RefreshCw size={12} className="animate-spin" />
                : <Trash2 size={12} />}
            </button>
          </div>
        </td>
      </tr>

      {/* Restore log (inline, below this row) */}
      {restoring && (
        <tr>
          <td colSpan={8} className="px-4 pb-3">
            <div className="bg-gray-900/60 border border-gray-800 rounded-lg overflow-hidden">
              <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-800/60">
                <Radio size={9} className="text-brand-400 animate-pulse" />
                <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-600">
                  Restoring {snap.stack_name}
                </span>
              </div>
              <div className="max-h-28 overflow-y-auto divide-y divide-gray-800/30 no-scrollbar">
                {restoreEvents.slice(0, 20).map((e, i) => {
                  const isDone  = e.event_type === 'restore.complete'
                  const isError = e.event_type === 'restore.error'
                  return (
                    <div key={i} className={clsx(
                      'flex items-center gap-2.5 px-3 py-1 text-[11px]',
                      isDone ? 'text-green-400' : isError ? 'text-red-400' : 'text-gray-400',
                    )}>
                      <span className="text-gray-700 tabular-nums shrink-0">
                        {(e.timestamp ?? '').slice(11, 19)}
                      </span>
                      {isDone && <CheckCircle size={9} className="shrink-0" />}
                      {isError && <AlertCircle size={9} className="shrink-0" />}
                      <span className="truncate">{e.message}</span>
                    </div>
                  )
                })}
                <div ref={logEndRef} />
              </div>
            </div>
          </td>
        </tr>
      )}

      {/* Dry-run log */}
      {isDryRunning && (
        <tr>
          <td colSpan={8} className="px-4 pb-3">
            <div className="bg-gray-900/60 border border-gray-800 rounded-lg overflow-hidden">
              <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-800/60">
                <FlaskConical size={9} className={dryRunDone ? 'text-blue-400' : 'text-blue-400 animate-pulse'} />
                <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-600">
                  {dryRunDone
                    ? restoreEvents[0]?.event_type === 'restore.error' ? 'Dry run failed' : 'Dry run complete'
                    : `Dry running ${snap.stack_name}`}
                </span>
              </div>
              <div className="max-h-28 overflow-y-auto divide-y divide-gray-800/30 no-scrollbar">
                {restoreEvents.slice(0, 20).map((e, i) => {
                  const isDone  = e.event_type === 'restore.complete'
                  const isError = e.event_type === 'restore.error'
                  return (
                    <div key={i} className={clsx(
                      'flex items-center gap-2.5 px-3 py-1 text-[11px]',
                      isDone ? 'text-blue-400' : isError ? 'text-red-400' : 'text-gray-400',
                    )}>
                      <span className="text-gray-700 tabular-nums shrink-0">
                        {(e.timestamp ?? '').slice(11, 19)}
                      </span>
                      {isDone && <CheckCircle size={9} className="shrink-0" />}
                      {isError && <AlertCircle size={9} className="shrink-0" />}
                      <span className="truncate">{e.message}</span>
                    </div>
                  )
                })}
                <div ref={logEndRef} />
              </div>
            </div>
          </td>
        </tr>
      )}

      {/* Confirmation modal */}
      {restoreConfirm && (
        <tr>
          <td colSpan={8}>
            <Modal
              title={`Restore ${snap.stack_name}`}
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
                    className="px-4 py-2 text-sm bg-red-600 hover:bg-red-700 text-white rounded-lg transition-colors flex items-center gap-2 disabled:opacity-60"
                  >
                    {restoreConfirmed.isPending
                      ? <RefreshCw size={13} className="animate-spin" />
                      : <RotateCcw size={13} />}
                    Confirm restore
                  </button>
                </>
              }
            >
              <div className="space-y-3">
                <p className="text-[13px] text-gray-300 leading-relaxed">
                  {restoreConfirm.message}
                </p>
                <div className="flex items-center gap-2 bg-amber-950/20 border border-amber-900/40 rounded-lg p-3 text-[12px] text-amber-400">
                  <AlertCircle size={13} className="shrink-0" />
                  Data loss window: ~{restoreConfirm.data_loss_window}
                </div>
              </div>
            </Modal>
          </td>
        </tr>
      )}
    </>
  )
}
