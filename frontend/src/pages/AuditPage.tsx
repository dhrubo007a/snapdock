import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ClipboardList, Download, ChevronLeft, ChevronRight, AlertCircle, RefreshCw } from 'lucide-react'
import clsx from 'clsx'
import { formatDistanceToNow } from 'date-fns'
import api from '../lib/api'

/** Parse a datetime string that may lack a timezone suffix (backend returns naive UTC). */
const parseUTC = (s: string) => new Date(s.endsWith('Z') || s.includes('+') ? s : s + 'Z')

const OUTCOME_BADGE: Record<string, string> = {
  success: 'bg-green-500/10 text-green-400 ring-1 ring-green-500/20',
  failure: 'bg-red-500/10 text-red-400 ring-1 ring-red-500/20',
  error:   'bg-red-500/10 text-red-400 ring-1 ring-red-500/20',
  denied:  'bg-yellow-500/10 text-yellow-400 ring-1 ring-yellow-500/20',
}

const PAGE_SIZE = 50

interface AuditLogEntry {
  id: number
  timestamp: string
  actor: string
  action: string
  target_stack: string | null
  target_snapshot: string | null
  outcome: string
  detail: string | null
}

export default function AuditPage() {
  const [page, setPage] = useState(0)
  const [stackFilter, setStackFilter] = useState('')

  const params: Record<string, string | number> = {
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  }
  if (stackFilter.trim()) params.stack_name = stackFilter.trim()

  const { data, isLoading, error, refetch, isFetching } = useQuery<AuditLogEntry[]>({
    queryKey: ['audit', stackFilter, page],
    queryFn: () => api.get('/audit', { params }).then((r) => r.data),
    retry: false,
  })

  const handleExport = () => {
    const qs = stackFilter.trim() ? `?stack_name=${encodeURIComponent(stackFilter.trim())}` : ''
    window.open(`/api/audit/export.csv${qs}`, '_blank')
  }

  const is403 = (error as any)?.response?.status === 403
  const isEmpty = !isLoading && !error && data?.length === 0
  const hasPrev = page > 0
  const hasNext = (data?.length ?? 0) === PAGE_SIZE

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <ClipboardList size={16} className="text-gray-500" />
          <div>
            <h1 className="text-lg font-semibold text-white">Audit Log</h1>
            <p className="text-[13px] text-gray-500">Admin-only action history</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw size={13} className={isFetching ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={handleExport}
            className="flex items-center gap-2 px-3 py-1.5 text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg transition-colors"
          >
            <Download size={13} />
            Export CSV
          </button>
        </div>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-3">
        <input
          type="text"
          value={stackFilter}
          onChange={(e) => { setStackFilter(e.target.value); setPage(0) }}
          placeholder="Filter by stack name…"
          className="w-56 bg-gray-900 border border-gray-700 text-gray-100 rounded-lg px-3 py-1.5 text-[13px] placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500/60"
        />
        {stackFilter && (
          <button
            onClick={() => { setStackFilter(''); setPage(0) }}
            className="text-[12px] text-gray-500 hover:text-gray-300 transition-colors"
          >
            Clear
          </button>
        )}
      </div>

      {/* 403 access denied */}
      {is403 && (
        <div className="flex items-start gap-3 bg-yellow-950/20 border border-yellow-900/50 rounded-xl p-4 text-sm">
          <AlertCircle size={15} className="text-yellow-400 mt-0.5 shrink-0" />
          <div>
            <p className="text-yellow-300 font-medium">Access denied</p>
            <p className="text-yellow-500/70 text-[12px] mt-0.5">The Audit Log is only accessible to administrators.</p>
          </div>
        </div>
      )}

      {/* Generic error */}
      {error && !is403 && (
        <div className="flex items-center gap-2 text-red-400 bg-red-950/20 border border-red-900/50 rounded-xl p-3 text-sm">
          <AlertCircle size={15} />
          Failed to load audit log
        </div>
      )}

      {/* Table */}
      {!error && (
        <div className="rounded-xl border border-gray-800 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-900/80">
                <tr>
                  {['Time', 'Actor', 'Action', 'Stack', 'Snapshot', 'Outcome', 'Detail'].map((h) => (
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
                {isLoading ? (
                  Array.from({ length: 8 }).map((_, i) => (
                    <tr key={i} className="animate-pulse">
                      {[100, 80, 120, 80, 100, 60, 160].map((w, j) => (
                        <td key={j} className="px-4 py-3">
                          <div className="h-3 bg-gray-800 rounded" style={{ width: w }} />
                        </td>
                      ))}
                    </tr>
                  ))
                ) : isEmpty ? (
                  <tr>
                    <td colSpan={7} className="text-center text-gray-600 text-sm py-12">
                      No audit entries found
                    </td>
                  </tr>
                ) : (
                  (data ?? []).map((entry) => (
                    <tr key={entry.id} className="hover:bg-gray-800/30 transition-colors">
                      <td className="px-4 py-2.5 text-[11px] text-gray-500 whitespace-nowrap">
                        {entry.timestamp
                          ? formatDistanceToNow(parseUTC(entry.timestamp), { addSuffix: true })
                          : '—'}
                      </td>
                      <td className="px-4 py-2.5 text-[12px] text-gray-300 font-medium">{entry.actor}</td>
                      <td className="px-4 py-2.5">
                        <span className="text-[11px] bg-gray-800 text-gray-300 px-2 py-0.5 rounded-full font-mono">
                          {entry.action}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-[12px] text-gray-400">{entry.target_stack ?? '—'}</td>
                      <td className="px-4 py-2.5 font-mono text-[11px] text-gray-500 max-w-[140px] truncate">
                        {entry.target_snapshot ?? '—'}
                      </td>
                      <td className="px-4 py-2.5">
                        <span className={clsx(
                          'text-[11px] font-medium px-2 py-0.5 rounded-full',
                          OUTCOME_BADGE[entry.outcome] ?? 'text-gray-400 bg-gray-800',
                        )}>
                          {entry.outcome}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-[11px] text-gray-600 max-w-[200px] truncate" title={entry.detail ?? ''}>
                        {entry.detail ?? '—'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Pagination */}
      {!error && (hasPrev || hasNext) && (
        <div className="flex items-center justify-between text-[12px] text-gray-500">
          <span>Page {page + 1}</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => p - 1)}
              disabled={!hasPrev}
              className="p-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft size={13} />
            </button>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={!hasNext}
              className="p-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronRight size={13} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
