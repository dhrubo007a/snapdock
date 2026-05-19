import { useQuery } from '@tanstack/react-query'
import { Shield, AlertTriangle, XCircle, FileDown, RefreshCw } from 'lucide-react'
import clsx from 'clsx'
import api from '../lib/api'
import type { CoverageDashboard, CoverageRow } from '../types'

// ── Skeletons ─────────────────────────────────────────────────────────────────
function PillSkeleton() {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl px-5 py-4 flex items-center gap-3 animate-pulse">
      <div className="w-6 h-6 bg-gray-800 rounded" />
      <div className="space-y-1.5">
        <div className="h-6 w-8 bg-gray-800 rounded" />
        <div className="h-3 w-16 bg-gray-800 rounded" />
      </div>
    </div>
  )
}

function RowSkeleton() {
  return (
    <tr className="animate-pulse">
      {[140, 80, 110, 90, 80].map((w, i) => (
        <td key={i} className="px-4 py-3">
          <div className="h-3 bg-gray-800 rounded" style={{ width: w }} />
        </td>
      ))}
    </tr>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function CoveragePage() {
  const { data, isLoading, error, refetch, isFetching } = useQuery<CoverageDashboard>({
    queryKey: ['coverage'],
    queryFn: () => api.get('/coverage').then((r) => r.data),
    refetchInterval: 30_000,
  })

  const summary = [
    { label: 'Protected',   count: data?.protected   ?? 0, color: 'text-green-400',  Icon: Shield        },
    { label: 'Overdue',     count: data?.overdue     ?? 0, color: 'text-yellow-400', Icon: AlertTriangle },
    { label: 'Unprotected', count: data?.unprotected ?? 0, color: 'text-red-400',    Icon: XCircle       },
  ]

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-white">Coverage</h1>
          <p className="text-[13px] text-gray-500 mt-0.5">Snapshot compliance across all stacks</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => refetch()}
            className="p-2 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
            title="Refresh"
          >
            <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} />
          </button>
          <a
            href="/api/coverage/export.pdf"
            download="snapdock-coverage.pdf"
            className="flex items-center gap-2 px-3 py-2 bg-gray-900 border border-gray-700 hover:border-gray-600 text-gray-300 hover:text-white rounded-lg text-sm transition-all duration-150"
          >
            <FileDown size={13} />
            Export PDF
          </a>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="text-red-400 bg-red-950/20 border border-red-900/50 rounded-xl p-3 text-sm">
          Failed to load coverage data
        </div>
      )}

      {/* Summary pills */}
      <div className="flex flex-wrap gap-3">
        {isLoading
          ? [0, 1, 2].map((i) => <PillSkeleton key={i} />)
          : summary.map(({ label, count, color, Icon }) => (
            <div
              key={label}
              className="bg-gray-900 border border-gray-800 rounded-xl px-5 py-4 flex items-center gap-3 hover:border-gray-700 transition-colors"
            >
              <Icon size={18} className={color} />
              <div>
                <p className={clsx('text-2xl font-bold leading-none', color)}>{count}</p>
                <p className="text-[11px] text-gray-500 mt-0.5">{label}</p>
              </div>
            </div>
          ))}
      </div>

      {/* Detail table */}
      <div className="rounded-xl border border-gray-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-900/80">
            <tr>
              {['Stack', 'Status', 'Last Clean Snapshot', 'Schedule', 'Last Verified'].map((h) => (
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
              ? [0, 1, 2, 3].map((i) => <RowSkeleton key={i} />)
              : (data?.rows ?? []).length === 0
                ? (
                  <tr>
                    <td colSpan={5} className="text-center text-gray-600 text-sm py-10">
                      No stacks detected
                    </td>
                  </tr>
                )
                : (data?.rows ?? []).map((row) => (
                  <CoverageTableRow key={row.stack_name} row={row} />
                ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Table row ─────────────────────────────────────────────────────────────────
function CoverageTableRow({ row }: { row: CoverageRow }) {
  const STATUS = {
    covered:     { label: 'Covered',     style: 'bg-green-500/10 text-green-400 ring-1 ring-green-500/20'  },
    overdue:     { label: 'Overdue',     style: 'bg-yellow-500/10 text-yellow-400 ring-1 ring-yellow-500/20' },
    unprotected: { label: 'Unprotected', style: 'bg-red-500/10 text-red-400 ring-1 ring-red-500/20'        },
  }
  const { label, style } = STATUS[row.status] ?? STATUS.unprotected

  return (
    <tr className="hover:bg-gray-800/30 transition-colors">
      <td className="px-4 py-3 font-medium text-[13px] text-gray-200">{row.stack_name}</td>
      <td className="px-4 py-3">
        <span className={clsx('text-[11px] font-semibold px-2 py-0.5 rounded-full', style)}>
          {label}
        </span>
      </td>
      <td className="px-4 py-3 text-[12px] text-gray-400">
        {row.last_clean_snap_at
          ? new Date(row.last_clean_snap_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
          : <span className="text-gray-600">never</span>}
      </td>
      <td className="px-4 py-3 text-[12px] text-gray-500 font-mono">
        {row.schedule_cron ?? <span className="text-gray-700">—</span>}
      </td>
      <td className="px-4 py-3 text-[12px] text-gray-500">
        {row.last_verified_at
          ? new Date(row.last_verified_at).toLocaleDateString()
          : <span className="text-gray-700">—</span>}
      </td>
    </tr>
  )
}

