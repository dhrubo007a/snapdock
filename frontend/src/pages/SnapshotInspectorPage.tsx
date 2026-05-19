import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Download } from 'lucide-react'
import clsx from 'clsx'
import { formatDistanceToNow } from 'date-fns'
import api from '../lib/api'
import type { SnapshotResponse } from '../types'

/** Parse a datetime string that may lack a timezone suffix (backend returns naive UTC). */
const parseUTC = (s: string) => new Date(s.endsWith('Z') || s.includes('+') ? s : s + 'Z')

const STATE_BADGE: Record<string, string> = {
  CLEAN:    'bg-green-500/10 text-green-400 ring-1 ring-green-500/20',
  DEGRADED: 'bg-yellow-500/10 text-yellow-400 ring-1 ring-yellow-500/20',
  BROKEN:   'bg-red-500/10 text-red-400 ring-1 ring-red-500/20',
}

function FieldSkeleton() {
  return (
    <div className="animate-pulse space-y-1.5">
      <div className="h-2.5 w-14 bg-gray-800 rounded" />
      <div className="h-4 w-28 bg-gray-800 rounded" />
    </div>
  )
}

export default function SnapshotInspectorPage() {
  const { stackName, snapshotId } = useParams<{ stackName: string; snapshotId: string }>()

  const { data: snap, isLoading, error } = useQuery<SnapshotResponse>({
    queryKey: ['snapshot', stackName, snapshotId],
    queryFn: () =>
      api.get(`/stacks/${stackName}/snapshots/${snapshotId}`).then((r) => r.data),
  })

  const { data: manifest } = useQuery({
    queryKey: ['manifest', stackName, snapshotId],
    queryFn: () =>
      api.get(`/stacks/${stackName}/snapshots/${snapshotId}/manifest`).then((r) => r.data),
    enabled: !!snap?.complete,
  })

  return (
    <div className="space-y-5 max-w-4xl animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link
          to={`/stacks/${stackName}/snapshots`}
          className="p-1.5 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
        >
          <ArrowLeft size={16} />
        </Link>
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold text-white">Snapshot Inspector</h1>
          <p className="text-[13px] text-gray-500 font-mono truncate">{snapshotId}</p>
        </div>
        {snap?.complete && (
          <a
            href={`/api/stacks/${stackName}/snapshots/${snapshotId}/export`}
            download
            className="flex items-center gap-2 px-3 py-2 bg-gray-900 border border-gray-700 hover:border-gray-600 text-gray-300 hover:text-white rounded-lg text-sm transition-all duration-150"
          >
            <Download size={13} />
            Export
          </a>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="text-red-400 bg-red-950/20 border border-red-900/50 rounded-xl p-3 text-sm">
          Snapshot not found or failed to load
        </div>
      )}

      {/* Metadata grid */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
        <h2 className="text-[11px] font-semibold uppercase tracking-widest text-gray-500 mb-4">Metadata</h2>
        {isLoading ? (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-5">
            {Array.from({ length: 9 }).map((_, i) => <FieldSkeleton key={i} />)}
          </div>
        ) : snap ? (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-5">
            <Field label="ID" value={snap.id} mono />
            <Field label="Stack" value={snap.stack_name} />
            <Field label="State">
              <span className={clsx('text-xs px-2 py-0.5 rounded-full', STATE_BADGE[snap.stack_state] ?? 'text-gray-400')}>
                {snap.stack_state}
              </span>
            </Field>
            <Field label="Trigger">
              <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">
                {snap.trigger_type}
              </span>
            </Field>
            <Field
              label="Generated"
              value={snap.generated_at
                ? formatDistanceToNow(parseUTC(snap.generated_at), { addSuffix: true })
                : '—'}
            />
            <Field
              label="Size"
              value={snap.size_bytes ? `${(snap.size_bytes / 1048576).toFixed(1)} MB` : '—'}
            />
            <Field label="Label" value={snap.label ?? '—'} />
            <Field label="Tags" value={snap.tags?.join(', ') || '—'} />
            <Field
              label="Verified"
              value={snap.verified
                ? `Yes${snap.verified_at ? ` (${snap.verified_at.slice(0, 10)})` : ''}`
                : 'No'}
            />
            <Field label="Complete" value={snap.complete ? 'Yes' : 'In progress…'} />
            <Field label="Locked" value={snap.locked ? 'Yes' : 'No'} />
          </div>
        ) : null}
      </div>

      {/* Manifest */}
      {manifest && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-800 flex items-center justify-between">
            <h2 className="text-[11px] font-semibold uppercase tracking-widest text-gray-500">Manifest</h2>
            <span className="text-[10px] text-gray-600">JSON</span>
          </div>
          <pre className="p-5 text-[11px] text-gray-300 overflow-x-auto leading-relaxed no-scrollbar">
            {JSON.stringify(manifest, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

// ── Field helper ──────────────────────────────────────────────────────────────
function Field({
  label,
  value,
  mono,
  children,
}: {
  label: string
  value?: string
  mono?: boolean
  children?: React.ReactNode
}) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-600 mb-1">{label}</p>
      {children ?? (
        <p className={clsx('text-[13px] text-gray-200 break-all', mono && 'font-mono text-[11px]')}>
          {value}
        </p>
      )}
    </div>
  )
}

