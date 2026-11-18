/** Audit log query + CSV export (PRD §2.5.2 / §6.5.5).
 *
 * Role guard is server-enforced (`/audit-logs` requires auditor or
 * system_admin). The UI also short-circuits with a "Forbidden" panel so
 * unauthorized users see a clean message instead of a stack of 403s.
 */
import { useState } from 'react'
import { Button, Input, Select, SortTh, TBody, THead, Table, TableMessage, TableScroll, Td, Tr, sortParams, useSort, Badge, cardClass } from '@uniops/shell'
import {
  Download, FileSearch, Filter, Loader2, AlertCircle, ChevronLeft, ChevronRight,
} from 'lucide-react'
import { useAuditLogs, useAuditFacets, buildAuditCsvPath, type AuditLogFilters } from '@/services/api'
import { formatDateTime, localToday } from '@/lib/utils'
import { plantWallClockToISO } from '@/lib/plantTime'
import { cn } from '@/lib/utils'

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
        <h1 className="text-2xl font-semibold text-fg">Audit log</h1>
        <Badge>
          {total}
        </Badge>
      </div>
      <p className="mt-1 text-sm text-fg-muted">
        Immutable record of every VMS change. Restricted to auditor / admin roles
        (PRD VMS-AU-003).
      </p>

      {/* Filters */}
      <form
        onSubmit={(e) => { e.preventDefault(); apply() }}
        className={cn(cardClass({ inset: true }), 'mt-5 grid grid-cols-1 gap-3 p-4 md:grid-cols-3')}
      >
        <Field label="Action type">
          <Select
            value={pending.action_type ?? ''}
            onChange={(e) => setPending({ ...pending, action_type: e.target.value || undefined })}
            className="w-full"
          >
            <option value="">All actions</option>
            {(facets?.action_types ?? []).map(a => <option key={a} value={a}>{a}</option>)}
          </Select>
        </Field>
        <Field label="Entity type">
          <Select
            value={pending.entity_type ?? ''}
            onChange={(e) => setPending({ ...pending, entity_type: e.target.value || undefined })}
            className="w-full"
          >
            <option value="">All entities</option>
            {(facets?.entity_types ?? []).map(e => <option key={e} value={e}>{e}</option>)}
          </Select>
        </Field>
        <Field label="User ID (UUID)">
          <Input
            value={pending.user_id ?? ''}
            onChange={(e) => setPending({ ...pending, user_id: e.target.value || undefined })}
            placeholder="optional"
            inputSize="sm" className="font-mono"
          />
        </Field>
        <Field label="From (date)">
          <Input
            type="date"
            value={pending.from ? localToday(new Date(pending.from)) : ''}
            onChange={(e) => setPending({
              ...pending,
              // Start of that day on the plant's clock (was UTC midnight —
              // 19:00/20:00 the evening before, here).
              from: e.target.value ? plantWallClockToISO(e.target.value, '00:00') : undefined,
            })}
          />
        </Field>
        <Field label="To (date)">
          <Input
            type="date"
            value={pending.to ? localToday(new Date(pending.to)) : ''}
            onChange={(e) => setPending({
              ...pending,
              to: e.target.value
                ? new Date(Date.parse(plantWallClockToISO(e.target.value, '23:59')) + 59_000).toISOString()
                : undefined,
            })}
          />
        </Field>
        <div className="flex items-end gap-2">
          <Button size="md"
            type="submit"
           
          >
            <Filter className="h-4 w-4" />
            Apply
          </Button>
          <Button variant="secondary" size="md"
            type="button"
            onClick={reset}
           
          >
            Reset
          </Button>
          <Button variant="secondary" size="md"
            type="button"
            onClick={downloadCsv}
            className="ml-auto"
          >
            <Download className="h-4 w-4" />
            CSV
          </Button>
        </div>
      </form>

      {/* Errors */}
      {error && (
        <p className="mt-4 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
          {error.message}
        </p>
      )}

      {/* Table */}
      <TableScroll className="mt-4">
        <Table>
          <THead>
            <Tr>
              <SortTh label="When" sortKey="timestamp" sort={sort} onSort={onSort} className="px-3 py-2.5" />
              <SortTh label="Who" sortKey="user_name" sort={sort} onSort={onSort} className="px-3 py-2.5" />
              <SortTh label="Action" sortKey="action_type" sort={sort} onSort={onSort} className="px-3 py-2.5" />
              <SortTh label="Entity" sortKey="entity_type" sort={sort} onSort={onSort} className="px-3 py-2.5" />
              <SortTh label="IP" sortKey="ip_address" sort={sort} onSort={onSort} className="px-3 py-2.5 hidden lg:table-cell" />
              <SortTh label="Notes" sortKey="notes" sort={sort} onSort={onSort} className="px-3 py-2.5" />
            </Tr>
          </THead>
          <TBody>
            {isLoading && (
              <TableMessage colSpan={6} className="py-6">
                <Loader2 className="inline h-4 w-4 animate-spin" /> Loading…
              </TableMessage>
            )}
            {!isLoading && items.length === 0 && (
              <TableMessage colSpan={6} className="py-12">
                No audit events match these filters.
              </TableMessage>
            )}
            {items.map(r => (
              <Tr key={r.id} className="align-top">
                <Td className="text-xs text-fg-secondary whitespace-nowrap font-mono">
                  {formatDateTime(r.timestamp)}
                </Td>
                <Td>
                  <p className="font-medium text-fg">{r.user_name}</p>
                  <p className="text-2xs font-mono text-fg-muted">{r.user_id.slice(0, 8)}…</p>
                </Td>
                <Td className="text-xs font-medium text-fg-secondary">
                  {r.action_type}
                </Td>
                <Td className="text-xs">
                  <p className="text-fg-secondary">{r.entity_type}</p>
                  <p className="font-mono text-2xs text-fg-muted">{r.entity_id.slice(0, 8)}…</p>
                </Td>
                <Td className="hidden lg:table-cell text-xs font-mono text-fg-muted">
                  {r.ip_address}
                </Td>
                <Td className="text-xs text-fg-secondary">
                  {r.notes || <span className="text-fg-faint">—</span>}
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableScroll>

      {/* Pagination */}
      {total > pageSize && (
        <div className="mt-3 flex items-center justify-end gap-2 text-xs text-fg-secondary">
          <Button variant="secondary" size="xs"
            onClick={() => setPage(page - 1)}
            disabled={page <= 1}
           
          >
            <ChevronLeft className="h-3 w-3" />
            Prev
          </Button>
          <span>Page {page} of {totalPages}</span>
          <Button variant="secondary" size="xs"
            onClick={() => setPage(page + 1)}
            disabled={page >= totalPages}
           
          >
            Next
            <ChevronRight className="h-3 w-3" />
          </Button>
        </div>
      )}
    </div>
  )
}

// ── Bits ────────────────────────────────────────────────────────────────────-

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-xs">
      <span className="block font-semibold text-fg-muted">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  )
}

