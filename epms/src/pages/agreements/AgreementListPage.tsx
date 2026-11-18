import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Search, Plus } from 'lucide-react'
import { SortTh, sortParams, useSort, Select, TBody, THead, Table, Td, Th, Tr } from '@uniops/shell'
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
  deposit: 'OneTime (Deposit)',
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

// GET /agreements sort keys (epms-api crud/agreement.py SORTABLE).
type SortField =
  | 'number' | 'title' | 'vendor_name' | 'agreement_type' | 'valid_from' | 'valid_to'
  | 'consumed_amount' | 'status'

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
        <span className={cn('amount font-medium', overCeiling ? 'text-danger-fg' : 'text-fg')}>
          {formatAmount(consumed, agreement.currency)}
        </span>
        <span className="amount text-fg-muted">
          {ceiling !== null ? `/ ${formatAmount(ceiling, agreement.currency)}` : 'No ceiling'}
        </span>
      </div>
      {ceiling !== null && (
        <div className="h-1.5 w-full rounded-full bg-surface-strong">
          <div
            style={{ width: `${pct ?? 0}%` }}
            className={cn(
              'h-full rounded-full transition-all',
              overCeiling ? 'bg-danger-solid' : (pct ?? 0) >= 90 ? 'bg-warning-solid' : 'bg-primary-500'
            )}
          />
        </div>
      )}
      {overCeiling && (
        <span className="text-2xs font-semibold text-danger-fg">Over ceiling</span>
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
  const [page, setPage] = useState(1)
  // Paged by the API, so the API sorts; a new order starts again at page 1.
  // Opens on Number ↓.
  const [sort, onSort] = useSort<SortField>({ key: 'number', dir: 'desc' }, () => setPage(1))
  const [pageSize, setPageSize] = useState(20)

  const { data: vendorsData } = useVendors({ active_only: true })
  const vendors = vendorsData?.items ?? []

  const { data, isLoading } = useAgreements({
    search: search || undefined,
    status: statusFilter !== 'all' ? (statusFilter as AgreementStatus) : undefined,
    vendor_id: vendorFilter !== 'all' ? vendorFilter : undefined,
    agreement_type: typeFilter !== 'all' ? (typeFilter as AgreementType) : undefined,
    ...sortParams(sort),
    page,
    page_size: pageSize,
  })
  const agreements = data?.items ?? []
  const total = data?.total ?? 0

  // Driven by the Access Control Matrix (epms.agreement.write), not a hardcoded
  // role list — the same key gates POST /agreements.
  const perms = useRolePermissions().data?.permissions
  const canCreate = user?.role === 'system_admin' || !!perms?.['epms.agreement.write']

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-fg">Agreements</h1>
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
      <div className="rounded-xl bg-surface shadow-sm p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
            <Input
              placeholder="Search agreement number, title, vendor…"
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
            value={vendorFilter}
            onChange={(e) => { setVendorFilter(e.target.value); setPage(1) }}
           
          >
            <option value="all">All Vendors</option>
            {vendors.map((v) => (
              <option key={v.id} value={v.id}>{v.name}</option>
            ))}
          </Select>
          <Select
            value={typeFilter}
            onChange={(e) => { setTypeFilter(e.target.value); setPage(1) }}
           
          >
            <option value="all">All Types</option>
            {(Object.entries(TYPE_LABELS) as [AgreementType, string][]).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
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
                <SortTh label="Number" sortKey="number" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Title" sortKey="title" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Type" sortKey="agreement_type" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Valid From" sortKey="valid_from" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Valid Until" sortKey="valid_to" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Consumed / NTE (pre-tax)" sortKey="consumed_amount" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-semibold text-fg-muted" />
                <Th />
              </Tr>
            </THead>
            <TBody>
              {isLoading && Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={9} />)}
              {!isLoading && agreements.length === 0 && (
                <Tr>
                  <Td align="center" colSpan={9}>
                    <div className="flex flex-col items-center gap-2 text-fg-muted">
                      <span className="text-3xl">📄</span>
                      <p className="text-sm font-medium">No agreements found</p>
                      <p className="text-xs">Try adjusting your search or filters</p>
                    </div>
                  </Td>
                </Tr>
              )}
              {agreements.map((agreement) => (
                <Tr
                  key={agreement.id}
                  className="bg-surface hover:bg-primary-50/60 transition-colors"
                >
                  <Td>
                    <Link to={`/agreements/${agreement.id}`} className="font-mono text-sm font-medium text-primary-700 hover:text-primary-900">
                      {agreement.number}
                    </Link>
                  </Td>
                  <Td>
                    <p className="font-medium text-fg">{agreement.title}</p>
                  </Td>
                  <Td className="text-fg-secondary">{agreement.vendor_name}</Td>
                  <Td>
                    <span className="text-xs text-fg-muted">{TYPE_LABELS[agreement.agreement_type]}</span>
                  </Td>
                  <Td className="text-xs text-fg-muted">{formatDate(agreement.valid_from)}</Td>
                  <Td className="text-xs text-fg-muted">{formatDate(agreement.valid_to)}</Td>
                  <Td>
                    <ConsumedCell agreement={agreement} />
                  </Td>
                  <Td>
                    <StatusBadge status={agreement.status as DocumentStatus} />
                  </Td>
                  <Td>
                    <Link to={`/agreements/${agreement.id}`}>
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
