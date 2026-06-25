/** Audit log query + CSV export (PRD §2.5.2 / §6.5.5).
 *
 * Role guard is server-enforced (`/audit-logs` requires auditor or
 * system_admin). The UI also short-circuits with a "Forbidden" panel so
 * unauthorized users see a clean message instead of a stack of 403s.
 */
import { useState } from 'react'
import {
  Download, FileSearch, Filter, Loader2, AlertCircle, ChevronLeft, ChevronRight,
} from 'lucide-react'
import { useAuditLogs, buildAuditCsvPath, type AuditLogFilters } from '@/services/api'
import { formatDateTime } from '@/lib/utils'

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

  const { data, isLoading, error } = useAuditLogs(applied)

  if (role && !ALLOWED_ROLES.has(role)) {
    return (
      <div className="max-w-md rounded-lg border border-red-200 bg-danger-50 p-5 text-sm text-danger-600">
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
    a.download = `vms-audit-log-${new Date().toISOString().slice(0, 10)}.csv`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  return (
    <div>
      <div className="flex items-center gap-2.5">
        <FileSearch className="h-6 w-6 text-primary-600" />
        <h1 className="text-2xl font-bold text-neutral-900">Audit log</h1>
        <span className="rounded-full bg-neutral-100 px-2.5 py-0.5 text-xs font-semibold text-neutral-600">
          {total}
        </span>
      </div>
      <p className="mt-1 text-sm text-neutral-500">
        Immutable record of every VMS change. Restricted to auditor / admin roles
        (PRD VMS-AU-003).
      </p>

      {/* Filters */}
      <form
        onSubmit={(e) => { e.preventDefault(); apply() }}
        className="mt-5 grid grid-cols-1 gap-3 rounded-lg border border-neutral-200 bg-white p-4 md:grid-cols-3"
      >
        <Field label="Action type">
          <select
            value={pending.action_type ?? ''}
            onChange={(e) => setPending({ ...pending, action_type: e.target.value || undefined })}
            className={inputCls}
          >
            <option value="">All actions</option>
            {ACTION_TYPES.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </Field>
        <Field label="Entity type">
          <select
            value={pending.entity_type ?? ''}
            onChange={(e) => setPending({ ...pending, entity_type: e.target.value || undefined })}
            className={inputCls}
          >
            <option value="">All entities</option>
            {ENTITY_TYPES.map(e => <option key={e} value={e}>{e}</option>)}
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
            value={pending.from?.slice(0, 10) ?? ''}
            onChange={(e) => setPending({
              ...pending,
              from: e.target.value ? new Date(e.target.value).toISOString() : undefined,
            })}
            className={inputCls}
          />
        </Field>
        <Field label="To (date)">
          <input
            type="date"
            value={pending.to?.slice(0, 10) ?? ''}
            onChange={(e) => setPending({
              ...pending,
              to: e.target.value
                ? new Date(`${e.target.value}T23:59:59`).toISOString()
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
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
          >
            Reset
          </button>
          <button
            type="button"
            onClick={downloadCsv}
            className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
          >
            <Download className="h-4 w-4" />
            CSV
          </button>
        </div>
      </form>

      {/* Errors */}
      {error && (
        <p className="mt-4 rounded-md border border-red-200 bg-danger-50 px-3 py-2 text-sm text-danger-600">
          {error.message}
        </p>
      )}

      {/* Table */}
      <div className="mt-4 overflow-x-auto rounded-lg border border-neutral-200 bg-white">
        <table className="min-w-full divide-y divide-neutral-200">
          <thead className="bg-neutral-50 text-left text-xs font-semibold uppercase tracking-wider text-neutral-500">
            <tr>
              <th className="px-3 py-2.5">When</th>
              <th className="px-3 py-2.5">Who</th>
              <th className="px-3 py-2.5">Action</th>
              <th className="px-3 py-2.5">Entity</th>
              <th className="px-3 py-2.5 hidden lg:table-cell">IP</th>
              <th className="px-3 py-2.5">Notes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100 bg-white text-sm">
            {isLoading && (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-neutral-400">
                <Loader2 className="inline h-4 w-4 animate-spin" /> Loading…
              </td></tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr><td colSpan={6} className="px-3 py-12 text-center text-neutral-400">
                No audit events match these filters.
              </td></tr>
            )}
            {items.map(r => (
              <tr key={r.id} className="hover:bg-primary-50/30 align-top">
                <td className="px-3 py-2 text-xs text-neutral-600 whitespace-nowrap font-mono">
                  {formatDateTime(r.timestamp)}
                </td>
                <td className="px-3 py-2">
                  <p className="font-medium text-neutral-900">{r.user_name}</p>
                  <p className="text-[10px] font-mono text-neutral-400">{r.user_id.slice(0, 8)}…</p>
                </td>
                <td className="px-3 py-2 text-xs font-medium text-neutral-700">
                  {r.action_type}
                </td>
                <td className="px-3 py-2 text-xs">
                  <p className="text-neutral-700">{r.entity_type}</p>
                  <p className="font-mono text-[10px] text-neutral-400">{r.entity_id.slice(0, 8)}…</p>
                </td>
                <td className="px-3 py-2 hidden lg:table-cell text-xs font-mono text-neutral-500">
                  {r.ip_address}
                </td>
                <td className="px-3 py-2 text-xs text-neutral-600">
                  {r.notes || <span className="text-neutral-300">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > pageSize && (
        <div className="mt-3 flex items-center justify-end gap-2 text-xs text-neutral-600">
          <button
            onClick={() => setPage(page - 1)}
            disabled={page <= 1}
            className="inline-flex items-center gap-1 rounded-md border border-neutral-300 bg-white px-2 py-1 disabled:opacity-40"
          >
            <ChevronLeft className="h-3 w-3" />
            Prev
          </button>
          <span>Page {page} of {totalPages}</span>
          <button
            onClick={() => setPage(page + 1)}
            disabled={page >= totalPages}
            className="inline-flex items-center gap-1 rounded-md border border-neutral-300 bg-white px-2 py-1 disabled:opacity-40"
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
  'w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-xs">
      <span className="block font-semibold uppercase tracking-wider text-neutral-500">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  )
}

// Keep this list in sync with the action_type strings emitted by crud/audit.py
// and the various endpoint handlers (visit.create, visit.check_in, etc.).
const ACTION_TYPES = [
  'visitor.create', 'visitor.update',
  'visit.create', 'visit.update', 'visit.cancel',
  'visit.check_in', 'visit.check_out',
  'badge.reprint',
  'badge_template.upsert', 'badge_template.update',
]

const ENTITY_TYPES = ['visitor', 'visit', 'badge_template']
