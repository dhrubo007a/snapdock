import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Download, ChevronDown, ChevronRight, CheckCircle, XCircle, AlertCircle, HardDrive, Cpu, FileText } from 'lucide-react'
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

const OUTCOME_ICON = {
  ok:      <CheckCircle size={11} className="text-green-400 shrink-0" />,
  failed:  <XCircle size={11} className="text-red-400 shrink-0" />,
  skipped: <AlertCircle size={11} className="text-gray-500 shrink-0" />,
}

function fmt(bytes: number | null | undefined) {
  if (!bytes) return '—'
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`
  return `${(bytes / 1024).toFixed(1)} KB`
}

function SectionHeader({ title, icon: Icon }: { title: string; icon: React.ElementType }) {
  return (
    <div className="px-5 py-3 border-b border-gray-800 flex items-center gap-2">
      <Icon size={13} className="text-gray-500" />
      <h2 className="text-[11px] font-semibold uppercase tracking-widest text-gray-500">{title}</h2>
    </div>
  )
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
  const [rawOpen, setRawOpen] = useState(false)

  const { data: snap, isLoading, error } = useQuery<SnapshotResponse>({
    queryKey: ['snapshot', stackName, snapshotId],
    queryFn: () =>
      api.get(`/stacks/${stackName}/snapshots/${snapshotId}`).then((r) => r.data),
  })

  const { data: manifest } = useQuery<any>({
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

      {/* Services */}
      {manifest?.services?.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <SectionHeader title="Services" icon={Cpu} />
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead className="bg-gray-900/80">
                <tr>
                  {['Service', 'Image', 'Quiesce', 'Pre-hook', 'Post-hook'].map((h) => (
                    <th key={h} className="px-4 py-2 text-left text-[10px] font-semibold uppercase tracking-widest text-gray-600">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {manifest.services.map((svc: any) => (
                  <tr key={svc.name} className="hover:bg-gray-800/20">
                    <td className="px-4 py-2 font-medium text-gray-200">{svc.name}</td>
                    <td className="px-4 py-2 font-mono text-gray-400 max-w-[240px] truncate">{svc.image}</td>
                    <td className="px-4 py-2">
                      {svc.quiesce ? (
                        <span className="flex items-center gap-1.5">
                          {OUTCOME_ICON[svc.quiesce_outcome as keyof typeof OUTCOME_ICON] ?? OUTCOME_ICON.skipped}
                          <span className="text-gray-400">{svc.quiesce}</span>
                        </span>
                      ) : <span className="text-gray-700">—</span>}
                    </td>
                    <td className="px-4 py-2">
                      {svc.pre_hook ? (
                        <span className="flex items-center gap-1.5">
                          {OUTCOME_ICON[svc.pre_hook_outcome as keyof typeof OUTCOME_ICON] ?? OUTCOME_ICON.skipped}
                          <span className="text-gray-500 font-mono text-[10px] truncate max-w-[120px]">{svc.pre_hook}</span>
                        </span>
                      ) : <span className="text-gray-700">—</span>}
                    </td>
                    <td className="px-4 py-2">
                      {svc.post_hook ? (
                        <span className="flex items-center gap-1.5">
                          {OUTCOME_ICON[svc.post_hook_outcome as keyof typeof OUTCOME_ICON] ?? OUTCOME_ICON.skipped}
                          <span className="text-gray-500 font-mono text-[10px] truncate max-w-[120px]">{svc.post_hook}</span>
                        </span>
                      ) : <span className="text-gray-700">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Volume Inventory */}
      {manifest?.volumes?.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <SectionHeader title="Volume Inventory" icon={HardDrive} />
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead className="bg-gray-900/80">
                <tr>
                  {['Volume', 'Type', 'Service', 'Mount Path', 'Size', 'Captured'].map((h) => (
                    <th key={h} className="px-4 py-2 text-left text-[10px] font-semibold uppercase tracking-widest text-gray-600">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {manifest.volumes.map((vol: any, i: number) => (
                  <tr key={i} className="hover:bg-gray-800/20">
                    <td className="px-4 py-2 font-mono text-gray-400 max-w-[160px] truncate">
                      {vol.name ?? vol.id?.slice(0, 12) ?? vol.host_path ?? '—'}
                    </td>
                    <td className="px-4 py-2">
                      <span className="text-[10px] bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">{vol.type}</span>
                    </td>
                    <td className="px-4 py-2 text-gray-300">{vol.service}</td>
                    <td className="px-4 py-2 font-mono text-gray-500 text-[11px]">{vol.mount_path}</td>
                    <td className="px-4 py-2 text-gray-500 whitespace-nowrap">{fmt(vol.size_bytes)}</td>
                    <td className="px-4 py-2">
                      {vol.type === 'tmpfs'
                        ? <span className="text-gray-600 text-[10px]">in-memory</span>
                        : vol.captured
                          ? <CheckCircle size={12} className="text-green-500" />
                          : <XCircle size={12} className="text-red-400" />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Diagnostics */}
      {manifest?.diagnostics && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <SectionHeader title="Diagnostics Capture" icon={FileText} />
          <div className="px-5 py-4 grid grid-cols-2 md:grid-cols-4 gap-4">
            <DiagField label="Log files" value={String(manifest.diagnostics.log_files?.length ?? 0)} />
            <DiagField label="Inspect files" value={String(manifest.diagnostics.inspect_files?.length ?? 0)} />
            <DiagField label="Hook log files" value={String(manifest.diagnostics.hook_log_files?.length ?? 0)} />
            <DiagField
              label="Captured"
              value={manifest.diagnostics.captured ? 'Yes' : 'No'}
              highlight={manifest.diagnostics.captured}
            />
          </div>
          {manifest.diagnostics.log_files?.length > 0 && (
            <div className="px-5 pb-4 space-y-1">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-600 mb-2">Captured files</p>
              <div className="flex flex-wrap gap-1.5">
                {[...manifest.diagnostics.log_files, ...manifest.diagnostics.inspect_files, ...manifest.diagnostics.hook_log_files].map((f: string, i: number) => (
                  <span key={i} className="font-mono text-[10px] bg-gray-800 text-gray-400 px-2 py-0.5 rounded">{f}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Raw manifest (collapsible) */}
      {manifest && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <button
            onClick={() => setRawOpen((v) => !v)}
            className="w-full px-5 py-3 flex items-center justify-between text-left hover:bg-gray-800/30 transition-colors"
          >
            <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-500">Raw Manifest JSON</span>
            {rawOpen ? <ChevronDown size={13} className="text-gray-600" /> : <ChevronRight size={13} className="text-gray-600" />}
          </button>
          {rawOpen && (
            <pre className="p-5 text-[11px] text-gray-300 overflow-x-auto leading-relaxed no-scrollbar border-t border-gray-800">
              {JSON.stringify(manifest, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────
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

function DiagField({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-600 mb-1">{label}</p>
      <p className={clsx('text-[13px] font-medium', highlight ? 'text-green-400' : 'text-gray-300')}>{value}</p>
    </div>
  )
}

