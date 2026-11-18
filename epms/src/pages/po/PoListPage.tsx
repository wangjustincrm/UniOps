import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Search, Plus, ExternalLink, RefreshCw, Package } from 'lucide-react'
import { SortTh, sortParams, useSort, Select, TBody, THead, Table, Td, Th, Tr } from '@uniops/shell'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { StatusBadge } from '@/components/ui/badge'
import { CurrentStepHint } from '@/components/ui/CurrentStepHint'
import { Pagination } from '@/components/ui/Pagination'
import { formatCAD, formatDate, cn } from '@/lib/utils'
import { SkeletonRow } from '@/components/ui/skeleton'
import { usePos } from '@/hooks/usePos'
import { useDepartments } from '@/hooks/useDepartments'
import { useAuthStore } from '@/stores/auth.store'
import type { DocumentStatus } from '@/types'
import type { PoStatus } from '@/services/po'
import { FeeStatusBadge } from '@/components/po/PoFeePanel'

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
  { value: 'nc_milk', label: 'Milk / NC' },
  { value: 'partially_received', label: 'Partial Receipt' },
  { value: 'fully_received', label: 'Fully Received' },
  { value: 'closed', label: 'Closed' },
  { value: 'cancelled', label: 'Cancelled' },
]

// GET /po sort keys (epms-api crud/po.py SORTABLE). Status sorts status →
// current approval step (chain order) → approver, across all pages.
type SortField =
  | 'number' | 'pr_number' | 'title' | 'type' | 'total' | 'status' | 'created_at'
  | 'is_prepaid' | 'expected_delivery'

export default function PoListPage() {
  const { user } = useAuthStore()
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [deptFilter, setDeptFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [prepaidFilter, setPrepaidFilter] = useState('all')
  const [page, setPage] = useState(1)
  // Paged by the API, so the API sorts; a new order starts again at page 1.
  // Starts on Created ↓ — the API's own default order.
  const [sort, onSort] = useSort<SortField>({ key: 'created_at', dir: 'desc' }, () => setPage(1))
  const [pageSize, setPageSize] = useState(20)

  const { data: deptData } = useDepartments()
  const departments = (deptData?.items ?? []).filter((d) => d.is_active)

  const { data, isLoading, refetch } = usePos({
    search: search || undefined,
    status: statusFilter !== 'all' ? (statusFilter as PoStatus) : undefined,
    department_id: deptFilter !== 'all' ? deptFilter : undefined,
    pr_type: typeFilter !== 'all' ? Number(typeFilter) : undefined,
    is_prepaid: prepaidFilter === 'all' ? undefined : prepaidFilter === 'yes',
    ...sortParams(sort),
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

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-fg">Purchase Orders</h1>
        <div className="flex items-center gap-2">
          {ncMsg && <span className="text-xs text-fg-muted">{ncMsg}</span>}
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
      <div className="rounded-xl bg-surface shadow-sm p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
            <Input
              placeholder="Search PO number, title, vendor, PR…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
              className="pl-9"
            />
          </div>
          <Select
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
           
          >
            {STATUS_FILTER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </Select>
          <Select
            value={deptFilter}
            onChange={(e) => { setDeptFilter(e.target.value); setPage(1) }}
           
          >
            <option value="all">All Departments</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </Select>
          <Select
            value={typeFilter}
            onChange={(e) => { setTypeFilter(e.target.value); setPage(1) }}
           
          >
            <option value="all">All Types</option>
            {Object.entries(TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </Select>
          <Select
            value={prepaidFilter}
            onChange={(e) => { setPrepaidFilter(e.target.value); setPage(1) }}
           
          >
            <option value="all">All (Prepaid)</option>
            <option value="yes">Prepaid</option>
            <option value="no">Not Prepaid</option>
          </Select>
          <span className="text-sm text-fg-muted">{isLoading ? 'Loading…' : `${total} result${total !== 1 ? 's' : ''}`}</span>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-xl bg-surface shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <Table>
            <THead>
              <Tr className="bg-surface-subtle">
                <SortTh label="PO Number" sortKey="number" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="PR Ref." sortKey="pr_number" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Title / Vendor" sortKey="title" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Type" sortKey="type" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Total (CAD)" sortKey="total" sort={sort} onSort={onSort} align="right"
                  className="px-4 py-3 text-right text-xs font-semibold text-fg-muted" />
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                {/* Freight / customs invoices of an NC order against its Incoterms: every
                    raw-material order, a milk order once it has Incoterms; blank otherwise. */}
                <Th
                    title="Freight & customs invoices against what the PO's Incoterms expect (NC orders; milk orders only once they have Incoterms)">
                  Fees
                </Th>
                <SortTh label="Created" sortKey="created_at" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Prepaid" sortKey="is_prepaid" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Exp. Delivery" sortKey="expected_delivery" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <Th />
              </Tr>
            </THead>
            <TBody>
              {isLoading && Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={11} />)}
              {!isLoading && pos.length === 0 && (
                <Tr>
                  <Td align="center" colSpan={11}>
                    <div className="flex flex-col items-center gap-2 text-fg-muted">
                      <Package aria-hidden className="h-8 w-8 stroke-[1.5] text-fg-faint" />
                      <p className="text-sm font-medium">No purchase orders found</p>
                      <p className="text-xs">Try adjusting your search or filters</p>
                    </div>
                  </Td>
                </Tr>
              )}
              {pos.map((po) => (
                <Tr
                  key={po.id}
                  className="bg-surface hover:bg-primary-50/60 transition-colors"
                >
                  <Td>
                    <Link to={`/po/${po.id}`} className="font-mono text-sm font-medium text-primary-700 hover:text-primary-900">
                      {po.number}
                    </Link>
                  </Td>
                  <Td>
                    {po.pr_id ? (
                      <Link
                        to={`/pr/${po.pr_id}`}
                        className="inline-flex items-center gap-1 font-mono text-xs text-fg-muted hover:text-primary-600"
                      >
                        {po.pr_number}
                        <ExternalLink className="h-3 w-3" />
                      </Link>
                    ) : (
                      <span className="text-fg-faint">—</span>
                    )}
                  </Td>
                  <Td>
                    <p className="font-medium text-fg">{po.title}</p>
                    <p className="text-xs text-fg-muted">{po.vendor_name}</p>
                  </Td>
                  <Td>
                    <span className="text-xs text-fg-muted">{TYPE_LABELS[po.type]}</span>
                  </Td>
                  <Td align="right">
                    <span className="amount font-medium text-fg">{formatCAD(po.total)}</span>
                  </Td>
                  <Td>
                    <StatusBadge status={po.status as DocumentStatus} />
                    <CurrentStepHint current_step={po.current_step} />
                  </Td>
                  <Td>
                    {po.fee_status && <FeeStatusBadge status={po.fee_status} />}
                  </Td>
                  <Td className="text-xs text-fg-muted">{formatDate(po.created_at)}</Td>
                  <Td>
                    {po.is_prepaid && (
                      <span className="inline-flex items-center rounded-full bg-warning-soft px-2 py-0.5 text-2xs font-semibold text-warning-fg border border-warning-line">
                        Prepaid
                      </span>
                    )}
                  </Td>
                  <Td className="text-xs text-fg-muted">{formatDate(po.expected_delivery ?? '')}</Td>
                  <Td>
                    <Link to={`/po/${po.id}`}>
                      <Button variant="ghost" size="sm">View</Button>
                    </Link>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>

        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      </div>
    </div>
  )
}
