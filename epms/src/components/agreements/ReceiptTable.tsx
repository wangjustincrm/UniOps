import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, XCircle, Ban, Paperclip } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { formatAmount, formatDate, cn } from '@/lib/utils'
import { useVoidReceipt, useApReviewReceipt } from '@/hooks/useAgreementReceipts'
import { agreementReceiptAttachmentService } from '@/services/agreementReceiptAttachments'
import type { ApiReceipt } from '@/services/agreementReceipts'
import type { ApiUserBrief } from '@/services/users'
import type { DocumentStatus } from '@/types'

// Named per Task 9 brief (E1 aging tolerance) rather than a magic 45 —
// an `open` receipt past this many days without being matched to an invoice
// gets a warning badge, and the table header rolls up how many are stale.
export const RECEIPT_AGING_DAYS = 45

// Statuses that can still be voided — the backend accepts DELETE from either
// (see agreement_receipt.py void()); 'reconciled'/'voided'/'rejected' are terminal.
// Exported so ReceiptListPage's row actions (Task 10 fix round 1) use the same
// set rather than re-typing it — this is a business rule, not a UI constant.
export const VOIDABLE_STATUSES = new Set<ApiReceipt['status']>(['open', 'pending_ap_review'])

function resolveUserName(users: ApiUserBrief[] | undefined, id: string | null): string | undefined {
  if (!id || !users) return undefined
  return users.find((u) => u.id === id)?.full_name
}

function isAged(receipt: ApiReceipt): boolean {
  if (receipt.status !== 'open') return false
  const days = (Date.now() - new Date(receipt.receipt_date).getTime()) / 86_400_000
  return days > RECEIPT_AGING_DAYS
}

// A DELIBERATELY SEPARATE top-level query-key namespace, NOT
// ['agreements', agreementId, 'receipts', ...] — react-query's invalidateQueries
// prefix-matches, so if this lived under the 'agreements' branch, narrowing
// the receipt mutations' invalidation to ['agreements', agreementId, 'receipts']
// (see useAgreementReceipts.ts) would still sweep every row's attachment query
// on every Void/Approve/Reject click, N requests at a time. Attachment
// metadata for a receipt doesn't change when the receipt's status changes, so
// there's nothing for those mutations to invalidate here anyway.
// Exported so ReceiptEntryForm can invalidate the exact same key after its
// post-create attachment upload — see the comment at that call site for why
// this must be kept in sync rather than each side hand-rolling its own copy.
export function receiptAttachmentsQueryKey(agreementId: string, receiptId: string) {
  return ['agreement-receipt-attachments', agreementId, receiptId] as const
}

function ReceiptAttachmentsCell({ agreementId, receiptId }: { agreementId: string; receiptId: string }) {
  const { data } = useQuery({
    queryKey: receiptAttachmentsQueryKey(agreementId, receiptId),
    queryFn: () => agreementReceiptAttachmentService.list(agreementId, receiptId),
    // Attachments are set once at receipt-creation time and essentially never
    // change afterward (no edit/delete UI exists yet) — a long staleTime
    // stops every re-mount/window-refocus from re-firing all N per-row
    // requests and re-flooding the browser's 6-connection-per-host queue.
    staleTime: 5 * 60_000,
  })
  const attachments = data ?? []
  if (attachments.length === 0) return <span className="text-neutral-300">—</span>
  return (
    <div className="flex flex-col gap-0.5">
      {attachments.map((att) => (
        <button
          key={att.id}
          type="button"
          onClick={() => agreementReceiptAttachmentService.download(agreementId, receiptId, att.id, att.filename)}
          className="inline-flex max-w-[9rem] items-center gap-1 truncate text-xs text-primary-600 hover:underline"
          title={att.filename}
        >
          <Paperclip className="h-3 w-3 shrink-0" />
          <span className="truncate">{att.filename}</span>
        </button>
      ))}
    </div>
  )
}

interface ReceiptTableProps {
  agreementId: string
  receipts: ApiReceipt[]
  users: ApiUserBrief[] | undefined
  currency: string
  // epms.agreement.receipt.write (the old epms.agreement.slip.write
  // permission key was renamed to this by Task 4) — same permission that gates
  // POST/PATCH/DELETE /receipts server-side (agreement_receipts.py
  // ReceiptRecordDep). Deliberately NOT epms.agreement.write —
  // recording/voiding a receipt is separate from editing the agreement itself.
  canWrite: boolean
  // epms.invoice.match (ApDep on the backend's ap-review route) — the AP
  // Approve/Reject buttons only render for holders of this permission.
  canApReview: boolean
  // Task 10: AgreementDetailPage's receipts section is now display-only —
  // recording lives on the standalone /receipts/new page, and Void/AP-review
  // are reserved for a future dedicated surface. When true this hides the
  // Actions column entirely (not just the buttons inside it), regardless of
  // what canWrite/canApReview would otherwise allow.
  readOnly?: boolean
}

export function ReceiptTable({ agreementId, receipts, users, currency, canWrite, canApReview, readOnly = false }: ReceiptTableProps) {
  const voidReceipt = useVoidReceipt(agreementId)
  const apReview = useApReviewReceipt(agreementId)
  // Mirrors ScheduleTable's pendingRowId convention: the hooks below are
  // single mutation objects shared by every row's button, so isPending alone
  // can't tell you WHICH row is mid-flight without this. pendingReviewKey
  // additionally encodes the ACTION (`${receiptId}:approve` vs `${receiptId}:reject`)
  // — a bare pendingReviewId flipped both buttons on the same row to
  // "Working…" together, so clicking Reject visibly lit up Approve instead.
  const [pendingVoidId, setPendingVoidId] = useState<string | null>(null)
  const [pendingReviewKey, setPendingReviewKey] = useState<string | null>(null)

  const agedCount = receipts.filter(isAged).length

  const handleVoid = (receiptId: string, receiptRef: string | null) => {
    // Voiding is terminal — 'voided' isn't in crud/agreement_receipt.py's
    // VOIDABLE set, so there is no undo. Same confirm() convention as the
    // other destructive actions in this app (PrDetailPage withdraw/recall,
    // BudgetCatalogPage delete, etc.).
    if (!confirm(`Void receipt ${receiptRef ?? '(no reference #)'}? This cannot be undone.`)) return
    setPendingVoidId(receiptId)
    voidReceipt.mutate(receiptId, { onSettled: () => setPendingVoidId(null) })
  }

  const handleReview = (receiptId: string, action: 'approve' | 'reject') => {
    setPendingReviewKey(`${receiptId}:${action}`)
    apReview.mutate({ receiptId, action }, { onSettled: () => setPendingReviewKey(null) })
  }

  if (receipts.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-neutral-400">
        No receipts recorded yet.
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {agedCount > 0 && (
        <div className="flex items-center gap-1.5 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-xs font-semibold text-warning-700">
          <AlertTriangle className="h-3.5 w-3.5" />
          {agedCount} receipt{agedCount === 1 ? '' : 's'} over {RECEIPT_AGING_DAYS} days unreconciled
        </div>
      )}
      <div className="rounded-lg border border-neutral-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Date</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Reference #</th>
                {/* Renders total_amount (tax-inclusive) — ReceiptEntryForm labels the
                    tax-exclusive field "Amount (before tax)", so this column must say
                    "Total", not "Amount", or the same word means two different things
                    on the two halves of this feature. */}
                <th className="px-4 py-2.5 text-right text-xs font-semibold text-neutral-500">Total</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Picked up by</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Status</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Attachments</th>
                {!readOnly && <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {receipts.map((receipt) => {
                const receivedByName = resolveUserName(users, receipt.received_by)
                const aged = isAged(receipt)
                const canVoid = !readOnly && canWrite && VOIDABLE_STATUSES.has(receipt.status)
                const canReview = !readOnly && canApReview && receipt.status === 'pending_ap_review'
                const rowVoidPending = voidReceipt.isPending && pendingVoidId === receipt.id
                const rowApprovePending = apReview.isPending && pendingReviewKey === `${receipt.id}:approve`
                const rowRejectPending = apReview.isPending && pendingReviewKey === `${receipt.id}:reject`
                return (
                  <tr key={receipt.id} className="border-b border-neutral-100 last:border-0 bg-white">
                    <td className="px-4 py-2.5 text-xs text-neutral-500">
                      <div className="flex items-center gap-1.5">
                        {formatDate(receipt.receipt_date)}
                        {aged && (
                          <span title={`Open ${RECEIPT_AGING_DAYS}+ days without reconciliation`}>
                            <AlertTriangle className="h-3.5 w-3.5 text-warning-600" />
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-neutral-700">{receipt.receipt_ref ?? '—'}</td>
                    <td className="px-4 py-2.5 text-right amount font-medium text-neutral-900">
                      {formatAmount(Number(receipt.total_amount), currency)}
                    </td>
                    <td className="px-4 py-2.5 text-neutral-900">{receivedByName ?? '—'}</td>
                    <td className="px-4 py-2.5">
                      <StatusBadge status={receipt.status as DocumentStatus} />
                      {/* The entire point of the AP-review step is to weigh this reason —
                          without it AP is just clicking Approve/Reject blind. Truncated
                          inline, full text on hover/title since there's no room for a
                          multi-line reason in a table cell. */}
                      {receipt.status === 'pending_ap_review' && receipt.missing_receipt_reason && (
                        <p
                          className="mt-1 max-w-[14rem] truncate text-xs text-neutral-500"
                          title={receipt.missing_receipt_reason}
                        >
                          {receipt.missing_receipt_reason}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <ReceiptAttachmentsCell agreementId={agreementId} receiptId={receipt.id} />
                    </td>
                    {!readOnly && (
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          {canReview && (
                            <>
                              <Button
                                size="sm"
                                variant="success-outline"
                                onClick={() => handleReview(receipt.id, 'approve')}
                                disabled={apReview.isPending}
                              >
                                <CheckCircle2 className="h-3.5 w-3.5" />
                                {rowApprovePending ? 'Working…' : 'Approve'}
                              </Button>
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => handleReview(receipt.id, 'reject')}
                                disabled={apReview.isPending}
                              >
                                <XCircle className="h-3.5 w-3.5" />
                                {rowRejectPending ? 'Working…' : 'Reject'}
                              </Button>
                            </>
                          )}
                          {canVoid && (
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => handleVoid(receipt.id, receipt.receipt_ref)}
                              disabled={voidReceipt.isPending}
                              className={cn('text-danger-600 hover:text-danger-700')}
                            >
                              <Ban className="h-3.5 w-3.5" />
                              {rowVoidPending ? 'Working…' : 'Void'}
                            </Button>
                          )}
                          {!canReview && !canVoid && <span className="text-neutral-300">—</span>}
                        </div>
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
