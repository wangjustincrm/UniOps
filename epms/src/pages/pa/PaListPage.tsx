import { useState, type ChangeEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, Search, FileText, CreditCard, Loader2, Truck } from 'lucide-react'
import { SortTh, sortParams, useSort } from '@uniops/shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { CurrentStepHint } from '@/components/ui/CurrentStepHint'
import { Pagination } from '@/components/ui/Pagination'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { usePas } from '@/hooks/usePas'
import { useDepartments } from '@/hooks/useDepartments'
import { useAuthStore } from '@/stores/auth.store'
import { useRolePermissions } from '@/hooks/useConfig'
import { useFreightAccess } from '@/hooks/useFreight'
import { useStaticTabNav } from '@/hooks/useStaticTabNav'
import type { PaStatus, ApiPa } from '@/services/pa'

// ─── Status config ────────────────────────────────────────────────────────────

const STATUS_CFG: Record<PaStatus, { label: string; variant: 'neutral' | 'warning' | 'info' | 'success' | 'danger'; dot: string }> = {
  draft:     { label: 'Draft',      variant: 'neutral', dot: 'bg-neutral-400'  },
  submitted: { label: 'Submitted',  variant: 'warning', dot: 'bg-warning-500'  },
  in_review: { label: 'In Review',  variant: 'info',    dot: 'bg-info-500'     },
  approved:  { label: 'Approved',   variant: 'success', dot: 'bg-success-600'  },
  processed: { label: 'Processed',  variant: 'neutral', dot: 'bg-neutral-600'  },
  returned:  { label: 'Returned',   variant: 'warning', dot: 'bg-warning-400'  },
  rejected:  { label: 'Rejected',   variant: 'danger',  dot: 'bg-danger-600'   },
  cancelled: { label: 'Cancelled',  variant: 'danger',  dot: 'bg-danger-600'   },
}

const STATUS_FILTER_OPTIONS = [
  { value: 'all',       label: 'All Statuses' },
  { value: 'draft',     label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'in_review', label: 'In Review' },
  { value: 'approved',  label: 'Approved' },
  { value: 'returned',  label: 'Returned' },
  { value: 'processed', label: 'Processed' },
  // Rejected = cancelled by a rejection (epms-api crud.pa.status_filter); a
  // rejected PA's stored status is cancelled, so it also stays under Cancelled.
  { value: 'rejected',  label: 'Rejected' },
  { value: 'cancelled', label: 'Cancelled' },
]

function PaStatusBadge({ status }: { status: PaStatus }) {
  // Tolerate unexpected/legacy status values so one bad row can't crash the list.
  const c = STATUS_CFG[status] ?? { label: String(status ?? '—'), variant: 'neutral' as const, dot: 'bg-neutral-400' }
  return (
    <Badge variant={c.variant}>
      <span className={cn('size-1.5 rounded-full', c.dot)} />
      {c.label}
    </Badge>
  )
}

const TYPE_BADGE_CFG: Record<ApiPa['pa_type'], { label: string; cls: string }> = {
  prepayment: { label: 'Prepayment', cls: 'bg-info-50 border-info-200 text-info-700 font-semibold' },
  settlement: { label: 'Settlement', cls: 'bg-success-50 border-success-200 text-success-700 font-semibold' },
  balance:    { label: 'Balance',    cls: 'bg-warning-50 border-warning-200 text-warning-700 font-semibold' },
  regular:    { label: 'Regular',    cls: 'bg-neutral-100 border-neutral-200 text-neutral-600 font-medium' },
}

function TypeBadge({ type }: { type: ApiPa['pa_type'] }) {
  const c = TYPE_BADGE_CFG[type] ?? TYPE_BADGE_CFG.regular
  return <span className={cn('inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide', c.cls)}>{c.label}</span>
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
          <h1 className="text-2xl font-bold text-neutral-900">Payment Applications</h1>
          <p className="text-sm text-neutral-500 mt-0.5">Manage payment requests and prepayment settlements</p>
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
          {canCreate && (
            <Button onClick={() => openTab('/pa/new')} className="gap-2">
              <Plus className="h-4 w-4" />
              New PA
            </Button>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="rounded-xl border border-neutral-200 bg-white shadow-sm overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-neutral-200 px-5 py-3">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
            <Input
              placeholder="Search PA #, vendor, PO…"
              value={search}
              onChange={(e: ChangeEvent<HTMLInputElement>) => { setSearch(e.target.value); setPage(1) }}
              className="pl-9"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => handleFilterChange(e.target.value)}
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
          <span className="text-sm text-neutral-400">
            {isPending ? 'Loading…' : `${total} result${total !== 1 ? 's' : ''}`}
          </span>
        </div>

        {isPending || (isFetching && pas.length === 0) ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <Loader2 className="h-8 w-8 text-neutral-300 mb-3 animate-spin" />
            <p className="text-sm text-neutral-400">Loading payment applications…</p>
          </div>
        ) : pas.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <CreditCard className="h-10 w-10 text-neutral-200 mb-3" />
            <p className="text-sm font-medium text-neutral-500">No payment applications found</p>
            <p className="text-xs text-neutral-400 mt-1">
              {search ? 'Try a different search term' : 'Create a new PA to get started'}
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-100 bg-neutral-50">
                {COLUMNS.map(({ label, key }) => (
                  <SortTh key={key} label={label} sortKey={key} sort={sort} onSort={onSort}
                    align={key === 'payment_amount' ? 'right' : 'left'}
                    className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap" />
                ))}
                <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap text-left" />
              </tr>
            </thead>
            <tbody>
              {pas.map((pa, idx) => (
                <tr
                  key={pa.id}
                  onClick={() => navigate(`/pa/${pa.id}`)}
                  className="border-b border-neutral-100 bg-white hover:bg-primary-50/60 cursor-pointer transition-colors"
                >
                  <td className="px-4 py-3">
                    <span className="font-mono text-xs font-semibold text-primary-700">{pa.pa_number}</span>
                    {pa.pa_type === 'prepayment' && pa.settlement_status === 'pending' && (
                      <div className="text-[10px] text-warning-600 font-medium mt-0.5">⚠ Settlement pending</div>
                    )}
                  </td>
                  <td className="px-4 py-3 font-medium text-neutral-900">{pa.vendor_name}</td>
                  <td className="px-4 py-3"><TypeBadge type={pa.pa_type} /></td>
                  <td className="px-4 py-3">
                    {/* A payment may settle several POs — list them all rather
                        than the header's primary one, which on its own reads as
                        though the other orders were not being paid. */}
                    {pa.source === 'freight' && (
                      <span className="inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary-700">Freight</span>
                    )}
                    {(pa.po_numbers?.length ? pa.po_numbers : (pa.po_number ? [pa.po_number] : [])).map((n) => (
                      <div key={n} className="font-mono text-xs text-neutral-600">{n}</div>
                    ))}
                    {pa.invoice_ids.length > 0 && (
                      <div className="text-[10px] text-neutral-400 mt-0.5">{pa.invoice_ids.length} invoice{pa.invoice_ids.length !== 1 ? 's' : ''}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs font-semibold text-neutral-900">
                    {formatAmount(pa.payment_amount, pa.currency)}
                    {pa.pa_type === 'prepayment' && pa.prepayment_pct && (
                      <div className="text-[10px] text-neutral-400 font-normal">{pa.prepayment_pct}% prepayment</div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{formatDate(pa.created_at)}</td>
                  <td className="px-4 py-3">
                    {/* A rejection cancels a PA; show it as what it was, so
                        the "Rejected" filter's rows say Rejected (ISS-0078). */}
                    <PaStatusBadge status={pa.rejected_in_approval ? 'rejected' : pa.status} />
                    <CurrentStepHint current_step={pa.current_step} />
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      to={`/pa/${pa.id}`}
                      onClick={(e) => e.stopPropagation()}
                      className="inline-flex items-center gap-1 text-xs text-primary-600 hover:underline"
                    >
                      <FileText className="h-3.5 w-3.5" />
                      View
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      </div>
    </div>
  )
}
