import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Search, Plus, ChevronUp, ChevronDown, ExternalLink, RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { StatusBadge } from '@/components/ui/badge'
import { CurrentStepHint } from '@/components/ui/CurrentStepHint'
import { Pagination } from '@/components/ui/Pagination'
import { formatCAD, formatDate, cn } from '@/lib/utils'
import { compareByStatusThenStep } from '@/lib/currentStepSort'
import { SkeletonRow } from '@/components/ui/skeleton'
import { usePos } from '@/hooks/usePos'
import { useDepartments } from '@/hooks/useDepartments'
import { useAuthStore } from '@/stores/auth.store'
import type { DocumentStatus } from '@/types'
import type { PoStatus } from '@/services/po'

const TYPE_LABELS: Record<number, string> = {
  1: 'Raw Mat./Pack.',
  2: 'Consumables',
  3: 'Spare Parts',
  4: 'Service',
  5: 'Fixed Asset',
  6: 'Project',
}

const STATUS_FILTER_OPTIONS = [
  { value: 'all', label: 'All Statuses' },
  { value: 'draft', label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'in_review', label: 'In Review' },
  { value: 'approved', label: 'Approved' },
  { value: 'returned', label: 'Returned' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'issued', label: 'Issued' },
  { value: 'nc_pending', label: 'NC Pending Approval' },
  { value: 'partially_received', label: 'Partial Receipt' },
  { value: 'fully_received', label: 'Fully Received' },
  { value: 'closed', label: 'Closed' },
  { value: 'cancelled', label: 'Cancelled' },
]

type SortField = 'number' | 'title' | 'total' | 'status' | 'created_at'
type SortDir = 'asc' | 'desc'

export default function PoListPage() {
  const { user } = useAuthStore()
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [deptFilter, setDeptFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [prepaidFilter, setPrepaidFilter] = useState('all')
  const [sortField, setSortField] = useState<SortField>('created_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const { data: deptData } = useDepartments()
  const departments = (deptData?.items ?? []).filter((d) => d.is_active)

  const { data, isLoading, refetch } = usePos({
    search: search || undefined,
    status: statusFilter !== 'all' ? (statusFilter as PoStatus) : undefined,
    department_id: deptFilter !== 'all' ? deptFilter : undefined,
    pr_type: typeFilter !== 'all' ? Number(typeFilter) : undefined,
    is_prepaid: prepaidFilter === 'all' ? undefined : prepaidFilter === 'yes',
    page,
    page_size: pageSize,
  })
  const pos = data?.items ?? []
  const total = data?.total ?? 0

  const canCreate = user?.role === 'procurement_officer' || user?.role === 'procurement_manager' || user?.role === 'system_admin'
  // Whether this user may trigger an NC sync is decided by the ENDPOINT and read
  // back from it — never re-derived here. The gate is a role UNION (base role
  // plus identity's user_roles grants), and `user.role` carries only the base
  // one: every erp_pa_officer in production holds `requester` as their base
  // role, so a hand-written check here would hide the button from exactly the
  // people who need it while a second copy of the list quietly drifted from the
  // server's. `can_sync` comes from GET /admin/nc-purchase-sync/status, which is
  // open to any authenticated user and computes it with the same helper the
  // POST gate uses.
  const { data: ncStatus } = useQuery({
    queryKey: ['nc-purchase-sync-can-sync'],
    queryFn: () => api.get<{ can_sync: boolean }>('/admin/nc-purchase-sync/status'),
    staleTime: 5 * 60 * 1000,
  })
  const canSyncNc = ncStatus?.can_sync === true

  const [ncSyncing, setNcSyncing] = useState(false)
  const [ncMsg, setNcMsg] = useState<string | null>(null)

  async function handleSyncNc() {
    setNcSyncing(true)
    setNcMsg(null)
    try {
      await api.post('/admin/nc-purchase-sync', { mode: 'incremental' })
      setNcMsg('NC sync started — refreshing shortly…')
      // the worker runs in the background (a few seconds); refresh the list after.
      setTimeout(() => { refetch(); setNcMsg(null) }, 8000)
    } catch (e) {
      setNcMsg(e instanceof Error ? e.message : 'NC sync failed')
      setTimeout(() => setNcMsg(null), 6000)
    } finally {
      setNcSyncing(false)
    }
  }

  const handleSort = (field: SortField) => {
    if (sortField === field) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortField(field); setSortDir('desc') }
  }

  const filtered = [...pos].sort((a, b) => {
    let cmp = 0
    if (sortField === 'total') cmp = a.total - b.total
    else if (sortField === 'created_at') cmp = a.created_at.localeCompare(b.created_at)
    else if (sortField === 'status') cmp = compareByStatusThenStep(a, b)
    else cmp = String(a[sortField as keyof typeof a]).localeCompare(String(b[sortField as keyof typeof b]))
    return sortDir === 'asc' ? cmp : -cmp
  })

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return <ChevronUp className="h-3 w-3 text-neutral-300" />
    return sortDir === 'asc'
      ? <ChevronUp className="h-3 w-3 text-primary-600" />
      : <ChevronDown className="h-3 w-3 text-primary-600" />
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-neutral-900">Purchase Orders</h1>
        <div className="flex items-center gap-2">
          {ncMsg && <span className="text-xs text-neutral-500">{ncMsg}</span>}
          {canSyncNc && (
            <Button variant="secondary" onClick={handleSyncNc} disabled={ncSyncing}>
              <RefreshCw className={cn('h-4 w-4', ncSyncing && 'animate-spin')} />
              {ncSyncing ? 'Syncing…' : 'Sync NC'}
            </Button>
          )}
          {canCreate && (
            <Link to="/po/new">
              <Button>
                <Plus className="h-4 w-4" />
                New PO
              </Button>
            </Link>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
            <Input
              placeholder="Search PO number, title, vendor, PR…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
              className="pl-9"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            {STATUS_FILTER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <select
            value={deptFilter}
            onChange={(e) => { setDeptFilter(e.target.value); setPage(1) }}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            <option value="all">All Departments</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
          <select
            value={typeFilter}
            onChange={(e) => { setTypeFilter(e.target.value); setPage(1) }}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            <option value="all">All Types</option>
            {Object.entries(TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <select
            value={prepaidFilter}
            onChange={(e) => { setPrepaidFilter(e.target.value); setPage(1) }}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            <option value="all">All (Prepaid)</option>
            <option value="yes">Prepaid</option>
            <option value="no">Not Prepaid</option>
          </select>
          <span className="text-sm text-neutral-400">{isLoading ? 'Loading…' : `${total} result${total !== 1 ? 's' : ''}`}</span>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <th
                  className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 cursor-pointer hover:text-neutral-700 select-none"
                  onClick={() => handleSort('number')}
                >
                  <span className="flex items-center gap-1">PO Number <SortIcon field="number" /></span>
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500">PR Ref.</th>
                <th
                  className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 cursor-pointer hover:text-neutral-700 select-none"
                  onClick={() => handleSort('title')}
                >
                  <span className="flex items-center gap-1">Title / Vendor <SortIcon field="title" /></span>
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500">Type</th>
                <th
                  className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 cursor-pointer hover:text-neutral-700 select-none"
                  onClick={() => handleSort('total')}
                >
                  <span className="flex items-center justify-end gap-1">Total (CAD) <SortIcon field="total" /></span>
                </th>
                <th
                  className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 cursor-pointer hover:text-neutral-700 select-none"
                  onClick={() => handleSort('status')}
                >
                  <span className="flex items-center gap-1">Status <SortIcon field="status" /></span>
                </th>
                <th
                  className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 cursor-pointer hover:text-neutral-700 select-none"
                  onClick={() => handleSort('created_at')}
                >
                  <span className="flex items-center gap-1">Created <SortIcon field="created_at" /></span>
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500">Prepaid</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500">Exp. Delivery</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {isLoading && Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={10} />)}
              {!isLoading && filtered.length === 0 && (
                <tr>
                  <td colSpan={10} className="px-4 py-16 text-center">
                    <div className="flex flex-col items-center gap-2 text-neutral-400">
                      <span className="text-3xl">📦</span>
                      <p className="text-sm font-medium">No purchase orders found</p>
                      <p className="text-xs">Try adjusting your search or filters</p>
                    </div>
                  </td>
                </tr>
              )}
              {filtered.map((po) => (
                <tr
                  key={po.id}
                  className="border-b border-neutral-100 last:border-0 bg-white hover:bg-primary-50/60 transition-colors"
                >
                  <td className="px-4 py-3">
                    <Link to={`/po/${po.id}`} className="font-mono text-sm font-medium text-primary-700 hover:text-primary-900">
                      {po.number}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    {po.pr_id ? (
                      <Link
                        to={`/pr/${po.pr_id}`}
                        className="inline-flex items-center gap-1 font-mono text-xs text-neutral-500 hover:text-primary-600"
                      >
                        {po.pr_number}
                        <ExternalLink className="h-3 w-3" />
                      </Link>
                    ) : (
                      <span className="text-neutral-300">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <p className="font-medium text-neutral-900">{po.title}</p>
                    <p className="text-xs text-neutral-400">{po.vendor_name}</p>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-neutral-500">{TYPE_LABELS[po.type]}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="amount font-medium text-neutral-900">{formatCAD(po.total)}</span>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={po.status as DocumentStatus} />
                    <CurrentStepHint current_step={po.current_step} />
                  </td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{formatDate(po.created_at)}</td>
                  <td className="px-4 py-3">
                    {po.is_prepaid && (
                      <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700 border border-amber-200">
                        Prepaid
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{formatDate(po.expected_delivery ?? '')}</td>
                  <td className="px-4 py-3">
                    <Link to={`/po/${po.id}`}>
                      <Button variant="ghost" size="sm">View</Button>
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      </div>
    </div>
  )
}
