import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Search, Receipt } from 'lucide-react'
import { StatusBadge } from '@/components/ui/badge'
import { Pagination } from '@/components/ui/Pagination'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { useAllReceipts } from '@/hooks/useAgreementReceipts'
import { useUserDirectory } from '@/hooks/useUsers'
import { RECEIPT_TYPE_LABELS } from '@/services/agreementReceipts'
import type { ApiReceiptWithAgreement, ReceiptStatus, ReceiptType } from '@/services/agreementReceipts'
import type { DocumentStatus } from '@/types'

// ─── Filters ─────────────────────────────────────────────────────────────────

const STATUS_FILTERS: { value: ReceiptStatus | 'all'; label: string }[] = [
  { value: 'all',                label: 'All' },
  { value: 'pending_ap_review',  label: 'Pending AP Review' },
  { value: 'open',                label: 'Open' },
  { value: 'reconciled',          label: 'Reconciled' },
  { value: 'rejected',            label: 'Rejected' },
  { value: 'voided',              label: 'Voided' },
]

const TYPE_FILTERS: { value: ReceiptType | 'all'; label: string }[] = [
  { value: 'all',           label: 'All Types' },
  { value: 'counter_slip',  label: RECEIPT_TYPE_LABELS.counter_slip },
  { value: 'delivery',      label: RECEIPT_TYPE_LABELS.delivery },
  { value: 'service',       label: RECEIPT_TYPE_LABELS.service },
]

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function ReceiptListPage() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<ReceiptStatus | 'all'>('all')
  const [typeFilter, setTypeFilter] = useState<ReceiptType | 'all'>('all')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const { data, isLoading } = useAllReceipts({
    search: search || undefined,
    status: statusFilter !== 'all' ? statusFilter : undefined,
    receipt_type: typeFilter !== 'all' ? typeFilter : undefined,
    page,
    page_size: pageSize,
  })
  const receipts = data?.items ?? []
  const total = data?.total ?? 0

  // Resolves received_by (a bare user UUID on the wire) into a display name.
  // useUserDirectory(), NOT useUsers() — GET /users/directory is open to any
  // authenticated caller; GET /users is system_admin-only and would 403 for
  // everyone else who can reach this page via epms.agreement.read.
  const { data: directory } = useUserDirectory()
  const userNames = useMemo(() => {
    const map = new Map<string, string>()
    for (const u of directory?.items ?? []) map.set(u.id, u.full_name)
    return map
  }, [directory])

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-neutral-900">Agreement Receipts</h1>
        <p className="mt-1 text-sm text-neutral-500">
          Counter slips, delivery notes, and service sign-offs recorded against house-account agreements
        </p>
      </div>

      {/* Filters */}
      <div className="rounded-xl border border-neutral-200 bg-white p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
        <div className="relative flex-1 min-w-0">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-neutral-400" />
          <input
            type="text"
            placeholder="Search by reference # or agreement #..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            className="w-full h-9 pl-9 pr-3 rounded-lg border border-neutral-300 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
          />
        </div>
        <select
          value={typeFilter}
          onChange={(e) => { setTypeFilter(e.target.value as ReceiptType | 'all'); setPage(1) }}
          className="h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
        >
          {TYPE_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
        <div className="flex flex-wrap gap-1.5">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => { setStatusFilter(f.value); setPage(1) }}
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
        {isLoading || receipts.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Receipt className="h-10 w-10 text-neutral-300 mb-3" />
            <p className="text-sm font-medium text-neutral-500">
              {isLoading ? 'Loading…' : 'No agreement receipts found'}
            </p>
            {!isLoading && <p className="text-xs text-neutral-400 mt-1">Try adjusting your filters</p>}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <Th>Date</Th>
                <Th>Type</Th>
                <Th>Reference #</Th>
                <Th>Agreement</Th>
                <Th align="right">Amount</Th>
                <Th>Received By</Th>
                <Th>Status</Th>
                <Th>Linked Invoice</Th>
              </tr>
            </thead>
            <tbody>
              {receipts.map((receipt) => (
                <ReceiptRow
                  key={receipt.id}
                  receipt={receipt}
                  receivedByName={userNames.get(receipt.received_by)}
                />
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

function Th({ children, align = 'left' }: { children: React.ReactNode; align?: 'left' | 'right' }) {
  return (
    <th
      className={cn(
        'px-4 py-3 text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap',
        align === 'right' ? 'text-right' : 'text-left'
      )}
    >
      {children}
    </th>
  )
}

function ReceiptRow({
  receipt, receivedByName,
}: {
  receipt: ApiReceiptWithAgreement
  receivedByName?: string
}) {
  return (
    <tr className="border-b border-neutral-100 bg-white hover:bg-primary-50/60 transition-colors">
      <td className="px-4 py-3 text-neutral-600">{formatDate(receipt.receipt_date)}</td>
      <td className="px-4 py-3 text-neutral-700">{RECEIPT_TYPE_LABELS[receipt.receipt_type]}</td>
      <td className="px-4 py-3 font-mono text-xs text-neutral-700">{receipt.receipt_ref ?? '—'}</td>
      <td className="px-4 py-3">
        {/* Never a bare agreement_id UUID — the parent agreement's human number,
            linking through to its detail page. */}
        <Link to={`/agreements/${receipt.agreement_id}`} className="text-primary-600 hover:underline font-mono text-xs">
          {receipt.agreement_number}
        </Link>
      </td>
      <td className="px-4 py-3 font-mono text-xs text-neutral-700 text-right">
        {/* This list spans multiple agreements, which can each be a
            different currency (fix round 1, Critical) — must use THIS row's
            own currency, never a hardcoded one. */}
        {formatAmount(Number(receipt.total_amount), receipt.currency)}
      </td>
      <td className="px-4 py-3 text-neutral-600">{receivedByName ?? '—'}</td>
      <td className="px-4 py-3">
        <StatusBadge status={receipt.status as DocumentStatus} />
      </td>
      <td className="px-4 py-3">
        {receipt.invoice_id ? (
          <Link to={`/invoices/${receipt.invoice_id}`} className="text-primary-600 hover:underline font-mono text-xs">
            {/* Never a bare UUID (fix round 1, Important 2) — fall back to a
                short id slice only when the server has no invoice_ref yet,
                same x_number ?? x_id.slice(0, 8) convention as
                InvoiceDetailPage.tsx's PO/agreement references. */}
            {receipt.invoice_ref ?? receipt.invoice_id.slice(0, 8)}
          </Link>
        ) : (
          <span className="text-neutral-300">—</span>
        )}
      </td>
    </tr>
  )
}
