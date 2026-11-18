import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, Search, Warehouse, ClipboardList } from 'lucide-react'
import { ChipGroup, Input, SortTh, TBody, THead, Table, Td, Tr, sortParams, type Sort, useSort } from '@uniops/shell'
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
  pending_ack:        { label: 'Pending Acknowledgement', variant: 'warning',  dot: 'bg-warning-solid' },
  collection_pending: { label: 'Collection Pending',      variant: 'warning',  dot: 'bg-warning-solid' },
  collected:          { label: 'Collected',               variant: 'success',  dot: 'bg-success-solid' },
  confirmed:          { label: 'Confirmed',               variant: 'success',  dot: 'bg-success-solid' },
  discrepancy:        { label: 'Discrepancy',             variant: 'danger',   dot: 'bg-danger-solid'  },
  cancelled:          { label: 'Cancelled',               variant: 'neutral',  dot: 'bg-fg-faint' },
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
          <h1 className="text-2xl font-semibold text-fg">Goods Receipt</h1>
          <p className="mt-1 text-sm text-fg-muted">Record received goods and service confirmations</p>
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
      <div className="rounded-xl border border-line bg-surface p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
        <div className="relative flex-1 min-w-0">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-fg-muted" />
          <Input inputSize="sm"
            type="text"
            placeholder="Search by GR#, PO#, vendor..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            className="w-full pl-9 pr-3"
          />
        </div>
        <div className="flex flex-wrap gap-1.5">
          <ChipGroup label="Status" items={STATUS_FILTERS.map((f) => ({ key: f.value, label: f.label }))} value={statusFilter} onChange={(k) => { setStatusFilter(k as GrStatus | 'all'); setPage(1) }} />
        </div>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-line bg-surface overflow-hidden">
        {isLoading || grs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Warehouse className="h-10 w-10 text-fg-faint mb-3" />
            <p className="text-sm font-medium text-fg-muted">
              {isLoading ? 'Loading…' : 'No goods receipts found'}
            </p>
            {!isLoading && <p className="text-xs text-fg-muted mt-1">Try adjusting your filters</p>}
          </div>
        ) : (
          <Table>
            <THead>
              <Tr className="bg-surface-subtle">
                <Th label="GR Number" sortKey="number" sort={sort} onSort={onSort} />
                <Th label="PO Reference" sortKey="po_number" sort={sort} onSort={onSort} />
                <Th label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} />
                <Th label="Type" sortKey="gr_type" sort={sort} onSort={onSort} />
                <Th label="Status" sortKey="status" sort={sort} onSort={onSort} />
                <Th label="Received Date" sortKey="received_at" sort={sort} onSort={onSort} />
                <Th label="Total Value" sortKey="total_value" sort={sort} onSort={onSort} align="right" />
                <Th label="Received By" sortKey="received_by" sort={sort} onSort={onSort} />
              </Tr>
            </THead>
            <TBody>
              {grs.map((gr, idx) => (
                <GrRow key={gr.id} gr={gr} idx={idx} />
              ))}
            </TBody>
          </Table>
        )}
      </div>

      <div className="rounded-xl border border-line bg-surface">
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
        'px-4 py-3 text-xs font-semibold text-fg-muted whitespace-nowrap',
        align === 'right' ? 'text-right' : 'text-left',
      )}
    />
  )
}

function GrRow({ gr, idx }: { gr: ApiGr; idx: number }) {
  const totalValue = gr.line_items.reduce((s, l) => s + Number(l.line_total), 0)

  return (
    <Tr className="bg-surface hover:bg-primary-50/60 transition-colors">
      <Td className="font-medium">
        <Link to={`/gr/${gr.id}`} className="text-primary-600 hover:underline font-mono text-xs">
          {gr.number}
        </Link>
      </Td>
      <Td>
        <Link to={`/po/${gr.po_id}`} className="text-primary-600 hover:underline font-mono text-xs">
          {gr.po_number}
        </Link>
      </Td>
      <Td className="text-fg-secondary">{gr.vendor_name}</Td>
      <Td>
        <Badge variant={gr.gr_type === 'physical' ? 'blue' : 'cat4'}>
          {gr.gr_type === 'physical' ? 'Physical' : 'Service'}
        </Badge>
      </Td>
      <Td>
        <GrStatusBadge status={gr.status} />
      </Td>
      <Td className="text-fg-secondary">{formatDate(gr.received_at)}</Td>
      <Td align="right" className="font-mono text-xs text-fg-secondary ">
        {formatAmount(totalValue, gr.currency)}
      </Td>
      <Td className="text-fg-secondary">{gr.received_by}</Td>
    </Tr>
  )
}
