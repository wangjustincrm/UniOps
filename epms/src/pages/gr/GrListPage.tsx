import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, Search, Warehouse, ClipboardList } from 'lucide-react'
import { SortTh, sortParams, useSort, type Sort } from '@uniops/shell'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Pagination } from '@/components/ui/Pagination'
import { cn, formatDate, formatAmount } from '@/lib/utils'
import { useGrs } from '@/hooks/useGrs'
import { useAuthStore } from '@/stores/auth.store'
import type { GrStatus } from '@/services/gr'
import type { ApiGr } from '@/services/gr'

// ─── GR Status badge ──────────────────────────────────────────────────────────

const GR_STATUS_CONFIG: Record<GrStatus, { label: string; variant: 'neutral' | 'warning' | 'info' | 'success' | 'danger'; dot: string }> = {
  pending_ack:        { label: 'Pending Acknowledgement', variant: 'warning',  dot: 'bg-warning-500' },
  collection_pending: { label: 'Collection Pending',      variant: 'warning',  dot: 'bg-warning-500' },
  collected:          { label: 'Collected',               variant: 'success',  dot: 'bg-success-600' },
  confirmed:          { label: 'Confirmed',               variant: 'success',  dot: 'bg-success-600' },
  discrepancy:        { label: 'Discrepancy',             variant: 'danger',   dot: 'bg-danger-600'  },
  cancelled:          { label: 'Cancelled',               variant: 'neutral',  dot: 'bg-neutral-400' },
}

export function GrStatusBadge({ status }: { status: GrStatus }) {
  const cfg = GR_STATUS_CONFIG[status]
  return (
    <Badge variant={cfg.variant}>
      <span className={cn('size-1.5 rounded-full', cfg.dot)} />
      {cfg.label}
    </Badge>
  )
}

// ─── Filters ─────────────────────────────────────────────────────────────────

// GET /gr sort keys (epms-api crud/gr.py SORTABLE).
type SortField =
  | 'number' | 'po_number' | 'vendor_name' | 'gr_type' | 'status' | 'received_at'
  | 'total_value' | 'received_by'

const STATUS_FILTERS: { value: GrStatus | 'all'; label: string }[] = [
  { value: 'all',                label: 'All' },
  { value: 'pending_ack',        label: 'Pending Ack.' },
  { value: 'collection_pending', label: 'Collection Pending' },
  { value: 'collected',          label: 'Collected' },
  { value: 'confirmed',          label: 'Confirmed' },
  { value: 'discrepancy',        label: 'Discrepancy' },
]

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function GrListPage() {
  const { user } = useAuthStore()
  const navigate = useNavigate()

  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<GrStatus | 'all'>('all')
  const [page, setPage] = useState(1)
  // Paged by the API, so the API sorts; a new order starts again at page 1.
  // Opens on Received Date ↓ (unreceived rows last).
  const [sort, onSort] = useSort<SortField>({ key: 'received_at', dir: 'desc' }, () => setPage(1))
  const [pageSize, setPageSize] = useState(20)

  const { data, isLoading } = useGrs({
    search: search || undefined,
    status: statusFilter !== 'all' ? statusFilter : undefined,
    ...sortParams(sort),
    page,
    page_size: pageSize,
  })
  const grs = data?.items ?? []
  const total = data?.total ?? 0

  // Requesters can create service GRs (type 4 PO service confirmation)
  const canCreate = ['warehouse_staff', 'procurement_officer', 'procurement_manager', 'system_admin', 'requester'].includes(user?.role ?? '')

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Goods Receipt</h1>
          <p className="mt-1 text-sm text-neutral-500">Record received goods and service confirmations</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => navigate('/gr/report')} className="gap-2">
            <ClipboardList className="h-4 w-4" />
            Receiving Report
          </Button>
          {canCreate && (
            <Button onClick={() => navigate('/gr/new')} className="gap-2">
              <Plus className="h-4 w-4" />
              New GR
            </Button>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="rounded-xl border border-neutral-200 bg-white p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
        <div className="relative flex-1 min-w-0">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-neutral-400" />
          <input
            type="text"
            placeholder="Search by GR#, PO#, vendor..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            className="w-full h-9 pl-9 pr-3 rounded-lg border border-neutral-300 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
          />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => { setStatusFilter(f.value as GrStatus | 'all'); setPage(1) }}
              className={cn(
                'px-3 py-1 rounded-full text-xs font-medium transition-colors',
                statusFilter === f.value
                  ? 'bg-primary-600 text-white'
                  : 'bg-neutral-100 text-neutral-600 hover:bg-neutral-200'
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading || grs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Warehouse className="h-10 w-10 text-neutral-300 mb-3" />
            <p className="text-sm font-medium text-neutral-500">
              {isLoading ? 'Loading…' : 'No goods receipts found'}
            </p>
            {!isLoading && <p className="text-xs text-neutral-400 mt-1">Try adjusting your filters</p>}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <Th label="GR Number" sortKey="number" sort={sort} onSort={onSort} />
                <Th label="PO Reference" sortKey="po_number" sort={sort} onSort={onSort} />
                <Th label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} />
                <Th label="Type" sortKey="gr_type" sort={sort} onSort={onSort} />
                <Th label="Status" sortKey="status" sort={sort} onSort={onSort} />
                <Th label="Received Date" sortKey="received_at" sort={sort} onSort={onSort} />
                <Th label="Total Value" sortKey="total_value" sort={sort} onSort={onSort} align="right" />
                <Th label="Received By" sortKey="received_by" sort={sort} onSort={onSort} />
              </tr>
            </thead>
            <tbody>
              {grs.map((gr, idx) => (
                <GrRow key={gr.id} gr={gr} idx={idx} />
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white">
        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      </div>
    </div>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function Th({
  label, sortKey, sort, onSort, align = 'left',
}: {
  label: string
  sortKey: SortField
  sort: Sort<SortField> | null
  onSort: (key: SortField) => void
  align?: 'left' | 'right'
}) {
  return (
    <SortTh
      label={label} sortKey={sortKey} sort={sort} onSort={onSort} align={align}
      className={cn(
        'px-4 py-3 text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap',
        align === 'right' ? 'text-right' : 'text-left',
      )}
    />
  )
}

function GrRow({ gr, idx }: { gr: ApiGr; idx: number }) {
  const totalValue = gr.line_items.reduce((s, l) => s + Number(l.line_total), 0)

  return (
    <tr className="border-b border-neutral-100 bg-white hover:bg-primary-50/60 transition-colors">
      <td className="px-4 py-3 font-medium">
        <Link to={`/gr/${gr.id}`} className="text-primary-600 hover:underline font-mono text-xs">
          {gr.number}
        </Link>
      </td>
      <td className="px-4 py-3">
        <Link to={`/po/${gr.po_id}`} className="text-primary-600 hover:underline font-mono text-xs">
          {gr.po_number}
        </Link>
      </td>
      <td className="px-4 py-3 text-neutral-700">{gr.vendor_name}</td>
      <td className="px-4 py-3">
        <span className={cn(
          'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
          gr.gr_type === 'physical' ? 'bg-blue-50 text-blue-700' : 'bg-purple-50 text-purple-700'
        )}>
          {gr.gr_type === 'physical' ? 'Physical' : 'Service'}
        </span>
      </td>
      <td className="px-4 py-3">
        <GrStatusBadge status={gr.status} />
      </td>
      <td className="px-4 py-3 text-neutral-600">{formatDate(gr.received_at)}</td>
      <td className="px-4 py-3 font-mono text-xs text-neutral-700 text-right">
        {formatAmount(totalValue, gr.currency)}
      </td>
      <td className="px-4 py-3 text-neutral-600">{gr.received_by}</td>
    </tr>
  )
}
