import { useState, type ChangeEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, Search, FileText, CreditCard, Loader2, Truck, FileInput, AlertTriangle } from 'lucide-react'
import { SortTh, sortParams, useSort, Select, TBody, THead, Table, Td, Th, Tr, type BadgeTone, Card } from '@uniops/shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { CurrentStepHint } from '@/components/ui/CurrentStepHint'
import { Pagination } from '@/components/ui/Pagination'
import { formatAmount, formatDate } from '@/lib/utils'
import { usePas } from '@/hooks/usePas'
import { useDepartments } from '@/hooks/useDepartments'
import { useAuthStore } from '@/stores/auth.store'
import { useRolePermissions } from '@/hooks/useConfig'
import { useFreightAccess } from '@/hooks/useFreight'
import { useStaticTabNav } from '@/hooks/useStaticTabNav'
import type { PaStatus, ApiPa } from '@/services/pa'

// ─── Status config ────────────────────────────────────────────────────────────

const STATUS_CFG: Record<PaStatus, { label: string; variant: BadgeTone }> = {
  draft:     { label: 'Draft',      variant: 'neutral'  },
  submitted: { label: 'Submitted',  variant: 'warning'  },
  in_review: { label: 'In Review',  variant: 'info'     },
  approved:  { label: 'Approved',   variant: 'success'  },
  processed: { label: 'Processed',  variant: 'dark'  },
  returned:  { label: 'Returned',   variant: 'warning'  },
  rejected:  { label: 'Rejected',   variant: 'danger'   },
  cancelled: { label: 'Cancelled',  variant: 'outline'   },
}

const STATUS_FILTER_OPTIONS = [
  { value: 'all',       label: 'All Statuses' },
  { value: 'draft',     label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'in_review', label: 'In Review' },
  { value: 'approved',  label: 'Approved' },
  { value: 'returned',  label: 'Returned' },
  { value: 'processed', label: 'Processed' },
  // No 'rejected': an approver's rejection cancels the PA (§3.15.6).
  { value: 'cancelled', label: 'Cancelled' },
]

function PaStatusBadge({ status }: { status: PaStatus }) {
  // Tolerate unexpected/legacy status values so one bad row can't crash the list.
  const c = STATUS_CFG[status] ?? { label: String(status ?? '—'), variant: 'neutral' as const }
  return (
    <Badge variant={c.variant} dot>
      {c.label}
    </Badge>
  )
}

const TYPE_BADGE_CFG: Record<ApiPa['pa_type'], { label: string; tone: BadgeTone }> = {
  prepayment: { label: 'Prepayment', tone: 'blue' },
  settlement: { label: 'Settlement', tone: 'success' },
  balance:    { label: 'Balance',    tone: 'warning' },
  regular:    { label: 'Regular',    tone: 'neutral' },
}

function TypeBadge({ type }: { type: ApiPa['pa_type'] }) {
  const c = TYPE_BADGE_CFG[type] ?? TYPE_BADGE_CFG.regular
  return <Badge size="sm" variant={c.tone}>{c.label}</Badge>
}

// GET /pa sort keys (epms-api crud/pa.py SORTABLE). Status sorts status →
// current approval step (chain order) → approver, across all pages, as PR/PO.
type SortField = 'pa_number' | 'vendor_name' | 'pa_type' | 'po_number' | 'payment_amount' | 'created_at' | 'status'

const COLUMNS: { label: string; key: SortField }[] = [
  { label: 'PA #', key: 'pa_number' },
  { label: 'Vendor', key: 'vendor_name' },
  { label: 'Type', key: 'pa_type' },
  { label: 'Linked PO / Invoice', key: 'po_number' },
  { label: 'Amount', key: 'payment_amount' },
  { label: 'Created', key: 'created_at' },
  { label: 'Status', key: 'status' },
]

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function PaListPage() {
  const { user } = useAuthStore()
  const navigate = useNavigate()

  const [statusFilter, setStatusFilter] = useState('all')
  const [deptFilter, setDeptFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  // Paged by the API, so the API sorts; a new order starts again at page 1.
  const [sort, onSort] = useSort<SortField>(null, () => setPage(1))

  const { data: deptData } = useDepartments()
  const departments = (deptData?.items ?? []).filter((d) => d.is_active)

  const { data, isPending, isFetching } = usePas({
    search: search || undefined,
    status: statusFilter !== 'all' ? (statusFilter as PaStatus) : undefined,
    department_id: deptFilter !== 'all' ? deptFilter : undefined,
    ...sortParams(sort),
    page,
    page_size: pageSize,
  })
  const pas = data?.items ?? []
  const total = data?.total ?? 0

  // Driven by the Access Control Matrix (epms.pa.write), not a hardcoded role
  // list: the same key gates POST /pa, so the button and the endpoint can no
  // longer disagree — and it picks up additional roles, which a base-role check
  // cannot see.
  const perms = useRolePermissions().data?.permissions
  const canCreate = user?.role === 'system_admin' || !!perms?.['epms.pa.write']
  // A Freight PA is gated on epms.freight.manage alone (api/v1/freight.py).
  const canFreight = !!useFreightAccess().data?.manage
  const { openTab } = useStaticTabNav()

  const handleFilterChange = (value: string) => {
    setStatusFilter(value)
    setPage(1)
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-fg">Payment Applications</h1>
          <p className="text-sm text-fg-muted mt-0.5">Manage payment requests and prepayment settlements</p>
        </div>
        <div className="flex items-center gap-2">
          {/* Both open the one static /pa/new tab, so each re-points it —
              otherwise New PA would just refocus a Freight form left open. */}
          {canFreight && (
            <Button variant={canCreate ? 'secondary' : 'primary'} onClick={() => openTab('/pa/new', 'source=freight')} className="gap-2">
              <Truck className="h-4 w-4" />
              New Freight PA
            </Button>
          )}
          {/* Direct PA: invoices matched to no PO and no agreement (Match →
              Direct PA on the invoice). Same epms.pa.write gate as POST /pa/direct. */}
          {canCreate && (
            <Button variant="secondary" onClick={() => openTab('/pa/new', 'source=direct')} className="gap-2">
              <FileInput className="h-4 w-4" />
              Create Direct PA
            </Button>
          )}
          {canCreate && (
            <Button onClick={() => openTab('/pa/new')} className="gap-2">
              <Plus className="h-4 w-4" />
              New PA
            </Button>
          )}
        </div>
      </div>

      {/* Filters */}
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
            <Input
              placeholder="Search PA #, vendor, PO…"
              value={search}
              onChange={(e: ChangeEvent<HTMLInputElement>) => { setSearch(e.target.value); setPage(1) }}
              className="pl-9"
            />
          </div>
          <Select
            value={statusFilter}
            onChange={(e) => handleFilterChange(e.target.value)}
           
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
          <span className="text-sm text-fg-muted">
            {isPending ? 'Loading…' : `${total} result${total !== 1 ? 's' : ''}`}
          </span>
        </div>

        {isPending || (isFetching && pas.length === 0) ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <Loader2 className="h-8 w-8 text-fg-faint mb-3 animate-spin" />
            <p className="text-sm text-fg-muted">Loading payment applications…</p>
          </div>
        ) : pas.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <CreditCard className="h-10 w-10 text-fg-faint mb-3" />
            <p className="text-sm font-medium text-fg-muted">No payment applications found</p>
            <p className="text-xs text-fg-muted mt-1">
              {search ? 'Try a different search term' : 'Create a new PA to get started'}
            </p>
          </div>
        ) : (
          <Table>
            <THead>
              <Tr className="bg-surface-subtle">
                {COLUMNS.map(({ label, key }) => (
                  <SortTh key={key} label={label} sortKey={key} sort={sort} onSort={onSort}
                    align={key === 'payment_amount' ? 'right' : 'left'}
                    className="px-4 py-3 text-xs font-semibold text-fg-muted whitespace-nowrap" />
                ))}
                <Th />
              </Tr>
            </THead>
            <TBody>
              {pas.map((pa, idx) => (
                <Tr
                  key={pa.id}
                  onClick={() => navigate(`/pa/${pa.id}`)}
                  className="bg-surface hover:bg-primary-50/60 cursor-pointer transition-colors"
                >
                  <Td>
                    <span className="font-mono text-xs font-semibold text-primary-700">{pa.pa_number}</span>
                    {pa.pa_type === 'prepayment' && pa.settlement_status === 'pending' && pa.status !== 'cancelled' && (
                      <div className="mt-0.5 flex items-center gap-1 text-2xs font-medium text-warning-fg"><AlertTriangle aria-hidden className="h-3 w-3" />Settlement pending</div>
                    )}
                  </Td>
                  <Td className="font-medium text-fg">{pa.vendor_name}</Td>
                  <Td><TypeBadge type={pa.pa_type} /></Td>
                  <Td>
                    {/* A payment may settle several POs — list them all rather
                        than the header's primary one, which on its own reads as
                        though the other orders were not being paid. */}
                    {pa.source === 'freight' && (
                      <Badge variant="accent" size="sm">Freight</Badge>
                    )}
                    {pa.source === 'direct' && (
                      <>
                        <Badge variant="warning" size="sm">Direct</Badge>
                        {pa.cost_center_code && (
                          <div className="font-mono text-2xs text-fg-muted mt-0.5">{pa.cost_center_code} · {pa.budget_account_code}</div>
                        )}
                      </>
                    )}
                    {(pa.po_numbers?.length ? pa.po_numbers : (pa.po_number ? [pa.po_number] : [])).map((n) => (
                      <div key={n} className="font-mono text-xs text-fg-secondary">{n}</div>
                    ))}
                    {pa.invoice_ids.length > 0 && (
                      <div className="text-2xs text-fg-muted mt-0.5">{pa.invoice_ids.length} invoice{pa.invoice_ids.length !== 1 ? 's' : ''}</div>
                    )}
                  </Td>
                  <Td className="font-mono text-xs font-semibold text-fg">
                    {formatAmount(pa.payment_amount, pa.currency)}
                    {pa.pa_type === 'prepayment' && pa.prepayment_pct && (
                      <div className="text-2xs text-fg-muted font-normal">{pa.prepayment_pct}% prepayment</div>
                    )}
                  </Td>
                  <Td className="text-xs text-fg-muted">{formatDate(pa.created_at)}</Td>
                  <Td>
                    <PaStatusBadge status={pa.status} />
                    <CurrentStepHint current_step={pa.current_step} />
                  </Td>
                  <Td>
                    <Link
                      to={`/pa/${pa.id}`}
                      onClick={(e) => e.stopPropagation()}
                      className="inline-flex items-center gap-1 text-xs text-primary-600 hover:underline"
                    >
                      <FileText className="h-3.5 w-3.5" />
                      View
                    </Link>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        )}

        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      </Card>
    </div>
  )
}
