import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Search, Plus, ChevronUp, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { StatusBadge } from '@/components/ui/badge'
import { Pagination } from '@/components/ui/Pagination'
import { SkeletonRow } from '@/components/ui/skeleton'
import { formatAmount, formatDate, cn } from '@/lib/utils'
import { useAgreements } from '@/hooks/useAgreements'
import { useVendors } from '@/hooks/useVendors'
import { useAuthStore } from '@/stores/auth.store'
import { useRolePermissions } from '@/hooks/useConfig'
import type { DocumentStatus } from '@/types'
import type { ApiAgreement, AgreementStatus, AgreementType } from '@/services/agreement'

const TYPE_LABELS: Record<AgreementType, string> = {
  house_account: 'House Account',
  recurring: 'Recurring',
  milestone: 'Milestone',
}

// Full AgreementStatus set, including 'submitted' and 'rejected' — omitting
// either leaves an agreement in that state unfilterable (an agreement waiting
// on its very first approval sits in 'submitted', not 'in_review'; see
// AgreementDetailPage's APPROVABLE_STATUSES comment for why).
const STATUS_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All Statuses' },
  { value: 'draft', label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'in_review', label: 'In Review' },
  { value: 'active', label: 'Active' },
  { value: 'expired', label: 'Expired' },
  { value: 'closed', label: 'Closed' },
  { value: 'returned', label: 'Returned' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'cancelled', label: 'Cancelled' },
]

type SortField = 'number' | 'title' | 'valid_to' | 'status'
type SortDir = 'asc' | 'desc'

// Consumed vs. Not-to-Exceed cell. Decimal fields arrive as JSON strings —
// every comparison/width calculation below wraps in Number(). The ceiling is
// a WARNING only (per product decision it never blocks any action), so
// `overCeiling` only changes styling, never disables anything downstream.
function ConsumedCell({ agreement }: { agreement: ApiAgreement }) {
  const consumed = Number(agreement.consumed_amount)
  const ceiling = agreement.not_to_exceed ? Number(agreement.not_to_exceed) : null
  const pct = ceiling && ceiling > 0 ? Math.min((consumed / ceiling) * 100, 100) : null
  const overCeiling = ceiling !== null && consumed > ceiling

  return (
    <div className="flex flex-col gap-1 min-w-[150px]">
      <div className="flex items-center justify-between text-xs">
        <span className={cn('amount font-medium', overCeiling ? 'text-danger-600' : 'text-neutral-900')}>
          {formatAmount(consumed, agreement.currency)}
        </span>
        <span className="amount text-neutral-400">
          {ceiling !== null ? `/ ${formatAmount(ceiling, agreement.currency)}` : 'No ceiling'}
        </span>
      </div>
      {ceiling !== null && (
        <div className="h-1.5 w-full rounded-full bg-neutral-200">
          <div
            style={{ width: `${pct ?? 0}%` }}
            className={cn(
              'h-full rounded-full transition-all',
              overCeiling ? 'bg-danger-500' : (pct ?? 0) >= 90 ? 'bg-warning-500' : 'bg-primary-500'
            )}
          />
        </div>
      )}
      {overCeiling && (
        <span className="text-[10px] font-semibold uppercase tracking-wide text-danger-600">Over ceiling</span>
      )}
    </div>
  )
}

export default function AgreementListPage() {
  const { user } = useAuthStore()
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [vendorFilter, setVendorFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [sortField, setSortField] = useState<SortField>('number')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const { data: vendorsData } = useVendors({ active_only: true })
  const vendors = vendorsData?.items ?? []

  const { data, isLoading } = useAgreements({
    search: search || undefined,
    status: statusFilter !== 'all' ? (statusFilter as AgreementStatus) : undefined,
    vendor_id: vendorFilter !== 'all' ? vendorFilter : undefined,
    agreement_type: typeFilter !== 'all' ? (typeFilter as AgreementType) : undefined,
    page,
    page_size: pageSize,
  })
  const agreements = data?.items ?? []
  const total = data?.total ?? 0

  // Driven by the Access Control Matrix (epms.agreement.write), not a hardcoded
  // role list — the same key gates POST /agreements.
  const perms = useRolePermissions().data?.permissions
  const canCreate = user?.role === 'system_admin' || !!perms?.['epms.agreement.write']

  const handleSort = (field: SortField) => {
    if (sortField === field) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortField(field); setSortDir('desc') }
  }

  const sorted = [...agreements].sort((a, b) => {
    let cmp = 0
    if (sortField === 'valid_to') cmp = a.valid_to.localeCompare(b.valid_to)
    else if (sortField === 'status') cmp = a.status.localeCompare(b.status)
    else cmp = String(a[sortField]).localeCompare(String(b[sortField]))
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
        <h1 className="text-2xl font-bold text-neutral-900">Agreements</h1>
        {canCreate && (
          <Link to="/agreements/new">
            <Button>
              <Plus className="h-4 w-4" />
              New Agreement
            </Button>
          </Link>
        )}
      </div>

      {/* Filters */}
      <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
            <Input
              placeholder="Search agreement number, title, vendor…"
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
            value={vendorFilter}
            onChange={(e) => { setVendorFilter(e.target.value); setPage(1) }}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            <option value="all">All Vendors</option>
            {vendors.map((v) => (
              <option key={v.id} value={v.id}>{v.name}</option>
            ))}
          </select>
          <select
            value={typeFilter}
            onChange={(e) => { setTypeFilter(e.target.value); setPage(1) }}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            <option value="all">All Types</option>
            {(Object.entries(TYPE_LABELS) as [AgreementType, string][]).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
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
                  <span className="flex items-center gap-1">Number <SortIcon field="number" /></span>
                </th>
                <th
                  className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 cursor-pointer hover:text-neutral-700 select-none"
                  onClick={() => handleSort('title')}
                >
                  <span className="flex items-center gap-1">Title <SortIcon field="title" /></span>
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500">Vendor</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500">Type</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500">Valid From</th>
                <th
                  className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 cursor-pointer hover:text-neutral-700 select-none"
                  onClick={() => handleSort('valid_to')}
                >
                  <span className="flex items-center gap-1">Valid Until <SortIcon field="valid_to" /></span>
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500">Consumed / Not to Exceed</th>
                <th
                  className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 cursor-pointer hover:text-neutral-700 select-none"
                  onClick={() => handleSort('status')}
                >
                  <span className="flex items-center gap-1">Status <SortIcon field="status" /></span>
                </th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {isLoading && Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={9} />)}
              {!isLoading && sorted.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-4 py-16 text-center">
                    <div className="flex flex-col items-center gap-2 text-neutral-400">
                      <span className="text-3xl">📄</span>
                      <p className="text-sm font-medium">No agreements found</p>
                      <p className="text-xs">Try adjusting your search or filters</p>
                    </div>
                  </td>
                </tr>
              )}
              {sorted.map((agreement) => (
                <tr
                  key={agreement.id}
                  className="border-b border-neutral-100 last:border-0 bg-white hover:bg-primary-50/60 transition-colors"
                >
                  <td className="px-4 py-3">
                    <Link to={`/agreements/${agreement.id}`} className="font-mono text-sm font-medium text-primary-700 hover:text-primary-900">
                      {agreement.number}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <p className="font-medium text-neutral-900">{agreement.title}</p>
                  </td>
                  <td className="px-4 py-3 text-neutral-700">{agreement.vendor_name}</td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-neutral-500">{TYPE_LABELS[agreement.agreement_type]}</span>
                  </td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{formatDate(agreement.valid_from)}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{formatDate(agreement.valid_to)}</td>
                  <td className="px-4 py-3">
                    <ConsumedCell agreement={agreement} />
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={agreement.status as DocumentStatus} />
                  </td>
                  <td className="px-4 py-3">
                    <Link to={`/agreements/${agreement.id}`}>
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
