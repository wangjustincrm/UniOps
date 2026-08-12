import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Search, Receipt, Plus, CheckCircle2, XCircle, Trash2, Paperclip, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StatusBadge, statusLabel } from '@/components/ui/badge'
import { Pagination } from '@/components/ui/Pagination'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth.store'
import { useRolePermissions } from '@/hooks/useConfig'
import { useAllReceipts, useVoidReceiptAny, useApReviewReceiptAny } from '@/hooks/useAgreementReceipts'
import { useUserDirectory } from '@/hooks/useUsers'
import { VOIDABLE_STATUSES } from '@/components/agreements/ReceiptTable'
import { RECEIPT_TYPE_LABELS } from '@/services/agreementReceipts'
import type { ApiReceiptWithAgreement, ReceiptStatus, ReceiptType } from '@/services/agreementReceipts'
import type { DocumentStatus } from '@/types'

// ─── Filters ─────────────────────────────────────────────────────────────────

// The VALUES here are the `status` query param this page sends to
// GET /agreement-receipts (crud/agreement_receipt.py list_all filters on it
// verbatim) and are also what `VOIDABLE_STATUSES` / StatusBadge below key off
// — never touch them. Only the WORDS are derived, via badge.tsx's
// statusLabel(), which reads the same STATUS_CONFIG the rows' own StatusBadge
// renders from. This list used to spell the labels out by hand, so the same
// status could be called one thing in this dropdown and another on the row two
// lines below it — which is exactly what nearly happened when 'Voided' became
// 'Removed'. Same shape TYPE_FILTERS below already uses for RECEIPT_TYPE_LABELS.
//
// 'all' is deliberately NOT in this array and keeps its own literal: it is a
// sentinel meaning "send no status param", not a status, and statusLabel('all')
// would fall through to its raw-value fallback and render "all".
const STATUS_FILTER_VALUES: ReceiptStatus[] = [
  'pending_ap_review', 'open', 'reconciled', 'rejected', 'voided',
]

const STATUS_FILTERS: { value: ReceiptStatus | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  ...STATUS_FILTER_VALUES.map((value) => ({ value, label: statusLabel(value) })),
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

  // Same two permission keys AgreementDetailPage used to gate the (now-removed)
  // inline entry form and the ReceiptTable's write props — Task 10 fix round 1
  // moves the actual Void/AP-review UI here, since ReceiptTable's only call
  // site is readOnly now and this cross-agreement list is otherwise the only
  // page that can reach every receipt regardless of which agreement it's on.
  const { user } = useAuthStore()
  const perms = useRolePermissions().data?.permissions
  const canRecordReceipt = user?.role === 'system_admin' || !!perms?.['epms.agreement.receipt.write']
  const canApReview = user?.role === 'system_admin' || !!perms?.['epms.invoice.match']

  const voidReceipt = useVoidReceiptAny()
  const apReview = useApReviewReceiptAny()
  // Mirrors ReceiptTable's pendingRowId convention — see that file's comment:
  // both mutations are single shared objects, so isPending alone can't say
  // WHICH row (or which of Approve/Reject) is mid-flight without this.
  const [pendingVoidId, setPendingVoidId] = useState<string | null>(null)
  const [pendingReviewKey, setPendingReviewKey] = useState<string | null>(null)

  const handleVoid = (agreementId: string, receiptId: string, receiptRef: string | null) => {
    if (!confirm(`Remove receipt ${receiptRef ?? '(no reference #)'}? This cannot be undone.`)) return
    setPendingVoidId(receiptId)
    voidReceipt.mutate({ agreementId, receiptId }, { onSettled: () => setPendingVoidId(null) })
  }

  const handleReview = (agreementId: string, receiptId: string, action: 'approve' | 'reject') => {
    // Reject has no way back: crud/agreement_receipt.py writes status =
    // "rejected", which is in RETIRED (not EDITABLE, not VOIDABLE), and
    // ap_review only accepts FROM pending_ap_review — there is no endpoint
    // that can move a rejected receipt anywhere else. Approve and Reject sit
    // 8px apart in the same row (fix round 2, Important): a mis-click here is
    // not "undo available", it's "re-key the whole receipt from scratch".
    if (action === 'reject' && !confirm(
      'Reject this receipt? This is final — a rejected receipt can never be approved, removed, or edited afterward. ' +
      'The only way to record this spend again is to enter a brand-new receipt.'
    )) return
    setPendingReviewKey(`${receiptId}:${action}`)
    apReview.mutate({ agreementId, receiptId, action }, { onSettled: () => setPendingReviewKey(null) })
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Agreement Receipts</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Counter slips, delivery notes, and service sign-offs recorded against house-account agreements
          </p>
        </div>
        {canRecordReceipt && (
          <Link to="/receipts/new">
            <Button className="gap-2">
              <Plus className="h-4 w-4" />
              New Receipt
            </Button>
          </Link>
        )}
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
                {/* Task 13: the merchant printed ON the slip, which is NOT
                    necessarily the vendor the house account is with. Showing
                    the agreement's own vendor here would have been free and
                    useless — it can never differ from the account it belongs
                    to, so it could never expose the mis-posting this column
                    exists to expose (shop A's slip charged to shop B's
                    account). */}
                <Th>Vendor on Receipt</Th>
                <Th>Agreement</Th>
                <Th align="right">Amount</Th>
                <Th>Received By</Th>
                <Th>Status</Th>
                {/* Whole-branch review (I2): AP approves/rejects from THIS
                    page and nowhere else, so the two facts that decision
                    rests on have to be on it — whether a photo was ever
                    attached (this column) and, for a pending_ap_review row,
                    the reason it was routed here (the sub-row below). Before
                    this, the only per-row information was date and amount. */}
                <Th>Evidence</Th>
                <Th>Linked Invoice</Th>
                <Th>Actions</Th>
              </tr>
            </thead>
            <tbody>
              {receipts.map((receipt) => {
                const canVoid = canRecordReceipt && VOIDABLE_STATUSES.has(receipt.status)
                const canReview = canApReview && receipt.status === 'pending_ap_review'
                return (
                  <ReceiptRow
                    key={receipt.id}
                    receipt={receipt}
                    receivedByName={userNames.get(receipt.received_by)}
                    canVoid={canVoid}
                    canReview={canReview}
                    voidPending={voidReceipt.isPending && pendingVoidId === receipt.id}
                    approvePending={apReview.isPending && pendingReviewKey === `${receipt.id}:approve`}
                    rejectPending={apReview.isPending && pendingReviewKey === `${receipt.id}:reject`}
                    anyVoidPending={voidReceipt.isPending}
                    anyReviewPending={apReview.isPending}
                    onVoid={() => handleVoid(receipt.agreement_id, receipt.id, receipt.receipt_ref)}
                    onApprove={() => handleReview(receipt.agreement_id, receipt.id, 'approve')}
                    onReject={() => handleReview(receipt.agreement_id, receipt.id, 'reject')}
                  />
                )
              })}
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
  receipt, receivedByName, canVoid, canReview,
  voidPending, approvePending, rejectPending, anyVoidPending, anyReviewPending,
  onVoid, onApprove, onReject,
}: {
  receipt: ApiReceiptWithAgreement
  receivedByName?: string
  canVoid: boolean
  canReview: boolean
  voidPending: boolean
  approvePending: boolean
  rejectPending: boolean
  anyVoidPending: boolean
  anyReviewPending: boolean
  onVoid: () => void
  onApprove: () => void
  onReject: () => void
}) {
  // Whole-branch review (I2): a pending_ap_review row gets a second, full-width
  // sub-row carrying missing_receipt_reason. Deliberately always-visible rather
  // than a collapsible/expandable table — the reviewer must not have to
  // discover that there is something to click to learn why this row is in
  // their queue, and no other row type has anything to show there.
  const showReason = receipt.status === 'pending_ap_review'
  const hasPhoto = receipt.attachment_count > 0
  return (
    <>
    <tr className={cn(
      'bg-white hover:bg-primary-50/60 transition-colors',
      showReason ? 'border-b-0' : 'border-b border-neutral-100',
    )}>
      <td className="px-4 py-3 text-neutral-600">{formatDate(receipt.receipt_date)}</td>
      <td className="px-4 py-3 text-neutral-700">{RECEIPT_TYPE_LABELS[receipt.receipt_type]}</td>
      <td className="px-4 py-3">
        {/* The way into the receipt itself (Task 12) — until this existed the
            only link on a row was the parent AGREEMENT's, so a recorder who
            needed to fix a receipt, add its photo, or just read its notes had
            nowhere to click. receipt_ref is optional, so the link text falls
            back to a label rather than disappearing: a row with no reference #
            must still be reachable, and it is exactly the kind of row (no
            paper slip) most likely to need editing. */}
        <Link
          to={`/receipts/${receipt.id}`}
          className={cn(
            'text-primary-600 hover:underline font-mono text-xs',
            !receipt.receipt_ref && 'italic text-neutral-500',
          )}
        >
          {receipt.receipt_ref ?? 'No reference #'}
        </Link>
      </td>
      <td className="px-4 py-3">
        {receipt.vendor_name ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-neutral-700">
            <span className="max-w-[10rem] truncate" title={receipt.vendor_name}>{receipt.vendor_name}</span>
            {/* Bound to the vendor master, or just the text off the slip? The
                verdict beside it means different things in the two cases (an
                id comparison vs a spelling comparison), so the reader has to
                be able to tell which one they are looking at. Muted grey and
                NOT an error colour: a one-off counter merchant with no
                master-data row is the normal case, and dressing it as a fault
                would train people to ignore the amber badge that isn't. */}
            {!receipt.vendor_matched && (
              <span
                className="rounded-full bg-neutral-100 px-1.5 py-0.5 text-[10px] font-medium text-neutral-500"
                title={`"${receipt.vendor_name}" is the text recorded from the receipt — it isn't linked to a vendor in the vendor list, so the check against agreement ${receipt.agreement_number} compares names, not records.`}
              >
                Text only
              </span>
            )}
            {/* A REMINDER, not an error — hence a badge on this one cell and
                deliberately NOT a red row: a slip from another trading name of
                the same group is a perfectly legal receipt. The title names
                both merchants, because "mismatch" on its own tells the reader
                nothing they can act on. The verdict itself is the server's
                (vendor_mismatch) — never recomputed here. */}
            {receipt.vendor_mismatch && (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-warning-50 px-1.5 py-0.5 text-[11px] font-medium text-warning-700"
                title={`This receipt is from "${receipt.vendor_name}", but agreement ${receipt.agreement_number} is with "${receipt.agreement_vendor_name}". Check it was charged to the right house account.`}
              >
                <AlertTriangle className="h-3 w-3" />
                Different vendor
              </span>
            )}
          </span>
        ) : (
          // Blank is not a problem to flag: OCR returns nothing when the slip
          // header is illegible, and a blank vendor is never a mismatch.
          <span className="text-neutral-300">—</span>
        )}
      </td>
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
        {hasPhoto ? (
          <span className="inline-flex items-center gap-1 text-xs text-neutral-600" title={`${receipt.attachment_count} file(s) attached`}>
            <Paperclip className="h-3.5 w-3.5" />
            {receipt.attachment_count}
          </span>
        ) : (
          // Not a neutral dash: "no photo" is the condition that sends a
          // receipt to AP review in the first place, and on an already-open
          // or reconciled row it is what a reviewer needs to notice.
          <span className="inline-flex items-center gap-1 text-xs font-medium text-warning-700" title="No photo or proof file attached to this receipt">
            <AlertTriangle className="h-3.5 w-3.5" />
            No photo
          </span>
        )}
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
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          {/* Void and Approve/Reject are two independent mutations sharing no
              lock — Task 10 fix round 2 (Minor): without this, mid-flight
              Void and mid-flight Approve/Reject could both be clicked on the
              same row. Neither ordering corrupts anything (a losing request
              just 409s or lands on a state its own button still permits), but
              disabling every button in this cell while EITHER mutation is in
              flight removes the race entirely rather than relying on that
              analysis holding up under future changes. */}
          {canReview && (
            <>
              <Button size="sm" variant="success-outline" onClick={onApprove} disabled={anyReviewPending || anyVoidPending}>
                <CheckCircle2 className="h-3.5 w-3.5" />
                {approvePending ? 'Working…' : 'Approve'}
              </Button>
              <Button size="sm" variant="secondary" onClick={onReject} disabled={anyReviewPending || anyVoidPending}>
                <XCircle className="h-3.5 w-3.5" />
                {rejectPending ? 'Working…' : 'Reject'}
              </Button>
            </>
          )}
          {canVoid && (
            <Button
              size="sm"
              variant="secondary"
              onClick={onVoid}
              disabled={anyVoidPending || anyReviewPending}
              className={cn('text-danger-600 hover:text-danger-700')}
            >
              <Trash2 className="h-3.5 w-3.5" />
              {voidPending ? 'Working…' : 'Remove'}
            </Button>
          )}
          {/* Always present: a reader with neither write permission still needs
              a way in, and this cell is where every other row action lives. */}
          <Link to={`/receipts/${receipt.id}`} className="text-xs text-primary-600 hover:underline">
            Open
          </Link>
        </div>
      </td>
    </tr>
    {showReason && (
      <tr className="border-b border-neutral-100 bg-warning-50/40">
        {/* colSpan must match the header's column count: Date, Type,
            Reference #, Vendor on Receipt, Agreement, Amount, Received By,
            Status, Evidence, Linked Invoice, Actions = 11. */}
        <td colSpan={11} className="px-4 pb-3 pt-0 text-xs text-warning-800">
          <span className="font-medium">Why this needs AP review: </span>
          {/* The reason is what create()/update() route a receipt to
              pending_ap_review on — a receipt can only get here by carrying
              one, so an empty reason means the row was moved by hand
              (Data Maintenance) and the reviewer must be told that rather
              than shown a blank. */}
          {receipt.missing_receipt_reason?.trim()
            ? receipt.missing_receipt_reason
            : <span className="italic">no reason was recorded on this receipt.</span>}
        </td>
      </tr>
    )}
    </>
  )
}
