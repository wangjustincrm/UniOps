/** Audit log query + CSV export (PRD §2.5.2 / §6.5.5).
 *
 * Role guard is server-enforced (`/audit-logs` requires auditor or
 * system_admin). The UI also short-circuits with a "Forbidden" panel so
 * unauthorized users see a clean message instead of a stack of 403s.
 */
import { useState } from 'react'
import { SortTh, useSort, sortParams } from '@uniops/shell'
import {
  Download, FileSearch, Filter, Loader2, AlertCircle, ChevronLeft, ChevronRight,
} from 'lucide-react'
import { useAuditLogs, useAuditFacets, buildAuditCsvPath, type AuditLogFilters } from '@/services/api'
import { formatDateTime, localToday } from '@/lib/utils'
import { plantWallClockToISO } from '@/lib/plantTime'

const ALLOWED_ROLES = new Set(['auditor', 'system_admin'])

// ── Role guard ──────────────────────────────────────────────────────────────-

function getRole(): string | null {
  try {
    for (const key of ['vms-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const state = raw ? JSON.parse(raw)?.state : null
      if (state?.user?.role) return state.user.role
    }
    return null
  } catch { return null }
}

function getToken(): string | null {
  try {
    for (const key of ['vms-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const token = raw ? JSON.parse(raw)?.state?.token : null
      if (token) return token
    }
    return null
  } catch { return null }
}

const VMS_API_BASE = (import.meta.env.VITE_API_URL as string | undefined) || ''

// ── Page ────────────────────────────────────────────────────────────────────-

export default function AuditLogPage() {
  // All hooks unconditionally first — bail-out UI renders below.
  const role = getRole()

  // Filter state — `pending` holds the form, `applied` drives the query.
  const [pending, setPending] = useState<AuditLogFilters>({ page: 1, page_size: 50 })
  const [applied, setApplied] = useState<AuditLogFilters>({ page: 1, page_size: 50 })

  // Server-sorted (the log pages). Kept out of `applied` so the CSV export
  // keeps its own chronological order.
  const [sort, onSort] = useSort<'timestamp' | 'user_name' | 'action_type' | 'entity_type' | 'ip_address' | 'notes'>(
    null, () => { setApplied(prev => ({ ...prev, page: 1 })); setPending(prev => ({ ...prev, page: 1 })) })
  const { data, isLoading, error } = useAuditLogs({ ...applied, ...sortParams(sort) })
  const { data: facets } = useAuditFacets()

  if (role && !ALLOWED_ROLES.has(role)) {
    return (
      <div className="max-w-md rounded-lg border border-danger-line bg-danger-soft p-5 text-sm text-danger-fg">
        <div className="flex items-center gap-2 font-semibold">
          <AlertCircle className="h-4 w-4" />
          Forbidden
        </div>
        <p className="mt-1.5 text-xs">
          The audit log is restricted to auditors and system administrators.
        </p>
      </div>
    )
  }

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const pageSize = applied.page_size ?? 50
  const page = applied.page ?? 1
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const apply = () => setApplied({ ...pending, page: 1 })

  const reset = () => {
    setPending({ page: 1, page_size: 50 })
    setApplied({ page: 1, page_size: 50 })
  }

  const setPage = (next: number) => {
    setApplied(prev => ({ ...prev, page: next }))
    setPending(prev => ({ ...prev, page: next }))
  }

  const downloadCsv = async () => {
    const path = buildAuditCsvPath(applied)
    const token = getToken()
    const res = await fetch(`${VMS_API_BASE}${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}))
      alert(`Could not export: ${detail.detail ?? res.statusText}`)
      return
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `vms-audit-log-${localToday()}.csv`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  return (
    <div>
      <div className="flex items-center gap-2.5">
        <FileSearch className="h-6 w-6 text-primary-600" />
        <h1 className="text-2xl font-bold text-fg">Audit log</h1>
        <span className="rounded-full bg-surface-sunken px-2.5 py-0.5 text-xs font-semibold text-fg-secondary">
          {total}
        </span>
      </div>
      <p className="mt-1 text-sm text-fg-muted">
        Immutable record of every VMS change. Restricted to auditor / admin roles
        (PRD VMS-AU-003).
      </p>

      {/* Filters */}
      <form
        onSubmit={(e) => { e.preventDefault(); apply() }}
        className="mt-5 grid grid-cols-1 gap-3 rounded-lg border border-line bg-surface p-4 md:grid-cols-3"
      >
        <Field label="Action type">
          <select
            value={pending.action_type ?? ''}
            onChange={(e) => setPending({ ...pending, action_type: e.target.value || undefined })}
            className={inputCls}
          >
            <option value="">All actions</option>
            {(facets?.action_types ?? []).map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </Field>
        <Field label="Entity type">
          <select
            value={pending.entity_type ?? ''}
            onChange={(e) => setPending({ ...pending, entity_type: e.target.value || undefined })}
            className={inputCls}
          >
            <option value="">All entities</option>
            {(facets?.entity_types ?? []).map(e => <option key={e} value={e}>{e}</option>)}
          </select>
        </Field>
        <Field label="User ID (UUID)">
          <input
            value={pending.user_id ?? ''}
            onChange={(e) => setPending({ ...pending, user_id: e.target.value || undefined })}
            placeholder="optional"
            className={inputCls + ' font-mono text-xs'}
          />
        </Field>
        <Field label="From (date)">
          <input
            type="date"
            value={pending.from ? localToday(new Date(pending.from)) : ''}
            onChange={(e) => setPending({
              ...pending,
              // Start of that day on the plant's clock (was UTC midnight —
              // 19:00/20:00 the evening before, here).
              from: e.target.value ? plantWallClockToISO(e.target.value, '00:00') : undefined,
            })}
            className={inputCls}
          />
        </Field>
        <Field label="To (date)">
          <input
            type="date"
            value={pending.to ? localToday(new Date(pending.to)) : ''}
            onChange={(e) => setPending({
              ...pending,
              to: e.target.value
                ? new Date(Date.parse(plantWallClockToISO(e.target.value, '23:59')) + 59_000).toISOString()
                : undefined,
            })}
            className={inputCls}
          />
        </Field>
        <div className="flex items-end gap-2">
          <button
            type="submit"
            className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700"
          >
            <Filter className="h-4 w-4" />
            Apply
          </button>
          <button
            type="button"
            onClick={reset}
            className="rounded-md border border-line-strong px-3 py-2 text-sm text-fg-secondary hover:bg-surface-subtle"
          >
            Reset
          </button>
          <button
            type="button"
            onClick={downloadCsv}
            className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3 py-2 text-sm text-fg-secondary hover:bg-surface-subtle"
          >
            <Download className="h-4 w-4" />
            CSV
          </button>
        </div>
      </form>

      {/* Errors */}
      {error && (
        <p className="mt-4 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
          {error.message}
        </p>
      )}

      {/* Table */}
      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="min-w-full divide-y divide-line">
          <thead className="bg-surface-subtle text-left text-xs font-semibold uppercase tracking-wider text-fg-muted">
            <tr>
              <SortTh label="When" sortKey="timestamp" sort={sort} onSort={onSort} className="px-3 py-2.5" />
              <SortTh label="Who" sortKey="user_name" sort={sort} onSort={onSort} className="px-3 py-2.5" />
              <SortTh label="Action" sortKey="action_type" sort={sort} onSort={onSort} className="px-3 py-2.5" />
              <SortTh label="Entity" sortKey="entity_type" sort={sort} onSort={onSort} className="px-3 py-2.5" />
              <SortTh label="IP" sortKey="ip_address" sort={sort} onSort={onSort} className="px-3 py-2.5 hidden lg:table-cell" />
              <SortTh label="Notes" sortKey="notes" sort={sort} onSort={onSort} className="px-3 py-2.5" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line-subtle bg-surface text-sm">
            {isLoading && (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-fg-muted">
                <Loader2 className="inline h-4 w-4 animate-spin" /> Loading…
              </td></tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr><td colSpan={6} className="px-3 py-12 text-center text-fg-muted">
                No audit events match these filters.
              </td></tr>
            )}
            {items.map(r => (
              <tr key={r.id} className="hover:bg-primary-50/30 align-top">
                <td className="px-3 py-2 text-xs text-fg-secondary whitespace-nowrap font-mono">
                  {formatDateTime(r.timestamp)}
                </td>
                <td className="px-3 py-2">
                  <p className="font-medium text-fg">{r.user_name}</p>
                  <p className="text-2xs font-mono text-fg-muted">{r.user_id.slice(0, 8)}…</p>
                </td>
                <td className="px-3 py-2 text-xs font-medium text-fg-secondary">
                  {r.action_type}
                </td>
                <td className="px-3 py-2 text-xs">
                  <p className="text-fg-secondary">{r.entity_type}</p>
                  <p className="font-mono text-2xs text-fg-muted">{r.entity_id.slice(0, 8)}…</p>
                </td>
                <td className="px-3 py-2 hidden lg:table-cell text-xs font-mono text-fg-muted">
                  {r.ip_address}
                </td>
                <td className="px-3 py-2 text-xs text-fg-secondary">
                  {r.notes || <span className="text-fg-faint">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > pageSize && (
        <div className="mt-3 flex items-center justify-end gap-2 text-xs text-fg-secondary">
          <button
            onClick={() => setPage(page - 1)}
            disabled={page <= 1}
            className="inline-flex items-center gap-1 rounded-md border border-line-strong bg-surface px-2 py-1 disabled:opacity-40"
          >
            <ChevronLeft className="h-3 w-3" />
            Prev
          </button>
          <span>Page {page} of {totalPages}</span>
          <button
            onClick={() => setPage(page + 1)}
            disabled={page >= totalPages}
            className="inline-flex items-center gap-1 rounded-md border border-line-strong bg-surface px-2 py-1 disabled:opacity-40"
          >
            Next
            <ChevronRight className="h-3 w-3" />
          </button>
        </div>
      )}
    </div>
  )
}

// ── Bits ────────────────────────────────────────────────────────────────────-

const inputCls =
  'w-full rounded-md border border-line-strong bg-surface px-3 py-2 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-xs">
      <span className="block font-semibold uppercase tracking-wider text-fg-muted">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  )
}

