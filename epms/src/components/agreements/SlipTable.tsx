import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, XCircle, Ban, Paperclip } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { formatAmount, formatDate, cn } from '@/lib/utils'
import { useVoidSlip, useApReviewSlip } from '@/hooks/useAgreementSlips'
import { agreementSlipAttachmentService } from '@/services/agreementSlipAttachments'
import type { ApiSlip } from '@/services/agreementSlips'
import type { ApiUserBrief } from '@/services/users'
import type { DocumentStatus } from '@/types'

// Named per Task 9 brief (E1 aging tolerance) rather than a magic 45 —
// an `open` slip past this many days without being matched to an invoice
// gets a warning badge, and the table header rolls up how many are stale.
export const SLIP_AGING_DAYS = 45

// Statuses that can still be voided — the backend accepts DELETE from either
// (see agreement_slip.py void()); 'reconciled'/'voided'/'rejected' are terminal.
const VOIDABLE_STATUSES = new Set<ApiSlip['status']>(['open', 'pending_ap_review'])

function resolveUserName(users: ApiUserBrief[] | undefined, id: string | null): string | undefined {
  if (!id || !users) return undefined
  return users.find((u) => u.id === id)?.full_name
}

function isAged(slip: ApiSlip): boolean {
  if (slip.status !== 'open') return false
  const days = (Date.now() - new Date(slip.slip_date).getTime()) / 86_400_000
  return days > SLIP_AGING_DAYS
}

// A DELIBERATELY SEPARATE top-level query-key namespace, NOT
// ['agreements', agreementId, 'slips', ...] — react-query's invalidateQueries
// prefix-matches, so if this lived under the 'agreements' branch, narrowing
// the slip mutations' invalidation to ['agreements', agreementId, 'slips']
// (see useAgreementSlips.ts) would still sweep every row's attachment query
// on every Void/Approve/Reject click, N requests at a time. Attachment
// metadata for a slip doesn't change when the slip's status changes, so
// there's nothing for those mutations to invalidate here anyway.
// Exported so SlipEntryForm can invalidate the exact same key after its
// post-create attachment upload — see the comment at that call site for why
// this must be kept in sync rather than each side hand-rolling its own copy.
export function slipAttachmentsQueryKey(agreementId: string, slipId: string) {
  return ['agreement-slip-attachments', agreementId, slipId] as const
}

function SlipAttachmentsCell({ agreementId, slipId }: { agreementId: string; slipId: string }) {
  const { data } = useQuery({
    queryKey: slipAttachmentsQueryKey(agreementId, slipId),
    queryFn: () => agreementSlipAttachmentService.list(agreementId, slipId),
    // Attachments are set once at slip-creation time and essentially never
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
          onClick={() => agreementSlipAttachmentService.download(agreementId, slipId, att.id, att.filename)}
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

interface SlipTableProps {
  agreementId: string
  slips: ApiSlip[]
  users: ApiUserBrief[] | undefined
  currency: string
  // epms.agreement.write — same permission that gates POST/DELETE /slips server-side.
  canWrite: boolean
  // epms.invoice.match (ApDep on the backend's ap-review route) — the AP
  // Approve/Reject buttons only render for holders of this permission.
  canApReview: boolean
}

export function SlipTable({ agreementId, slips, users, currency, canWrite, canApReview }: SlipTableProps) {
  const voidSlip = useVoidSlip(agreementId)
  const apReview = useApReviewSlip(agreementId)
  // Mirrors ScheduleTable's pendingRowId convention: the hooks below are
  // single mutation objects shared by every row's button, so isPending alone
  // can't tell you WHICH row is mid-flight without this. pendingReviewKey
  // additionally encodes the ACTION (`${slipId}:approve` vs `${slipId}:reject`)
  // — a bare pendingReviewId flipped both buttons on the same row to
  // "Working…" together, so clicking Reject visibly lit up Approve instead.
  const [pendingVoidId, setPendingVoidId] = useState<string | null>(null)
  const [pendingReviewKey, setPendingReviewKey] = useState<string | null>(null)

  const agedCount = slips.filter(isAged).length

  const handleVoid = (slipId: string, slipRef: string | null) => {
    // Voiding is terminal — 'voided' isn't in crud/agreement_slip.py's
    // VOIDABLE set, so there is no undo. Same confirm() convention as the
    // other destructive actions in this app (PrDetailPage withdraw/recall,
    // BudgetCatalogPage delete, etc.).
    if (!confirm(`Void pickup slip ${slipRef ?? '(no reference #)'}? This cannot be undone.`)) return
    setPendingVoidId(slipId)
    voidSlip.mutate(slipId, { onSettled: () => setPendingVoidId(null) })
  }

  const handleReview = (slipId: string, action: 'approve' | 'reject') => {
    setPendingReviewKey(`${slipId}:${action}`)
    apReview.mutate({ slipId, action }, { onSettled: () => setPendingReviewKey(null) })
  }

  if (slips.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-neutral-400">
        No pickup slips recorded yet.
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {agedCount > 0 && (
        <div className="flex items-center gap-1.5 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-xs font-semibold text-warning-700">
          <AlertTriangle className="h-3.5 w-3.5" />
          {agedCount} slip{agedCount === 1 ? '' : 's'} over {SLIP_AGING_DAYS} days unreconciled
        </div>
      )}
      <div className="rounded-lg border border-neutral-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Date</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Reference #</th>
                {/* Renders total_amount (tax-inclusive) — SlipEntryForm labels the
                    tax-exclusive field "Amount (before tax)", so this column must say
                    "Total", not "Amount", or the same word means two different things
                    on the two halves of this feature. */}
                <th className="px-4 py-2.5 text-right text-xs font-semibold text-neutral-500">Total</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Picked up by</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Status</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Attachments</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Actions</th>
              </tr>
            </thead>
            <tbody>
              {slips.map((slip) => {
                const pickedByName = resolveUserName(users, slip.picked_by)
                const aged = isAged(slip)
                const canVoid = canWrite && VOIDABLE_STATUSES.has(slip.status)
                const canReview = canApReview && slip.status === 'pending_ap_review'
                const rowVoidPending = voidSlip.isPending && pendingVoidId === slip.id
                const rowApprovePending = apReview.isPending && pendingReviewKey === `${slip.id}:approve`
                const rowRejectPending = apReview.isPending && pendingReviewKey === `${slip.id}:reject`
                return (
                  <tr key={slip.id} className="border-b border-neutral-100 last:border-0 bg-white">
                    <td className="px-4 py-2.5 text-xs text-neutral-500">
                      <div className="flex items-center gap-1.5">
                        {formatDate(slip.slip_date)}
                        {aged && (
                          <span title={`Open ${SLIP_AGING_DAYS}+ days without reconciliation`}>
                            <AlertTriangle className="h-3.5 w-3.5 text-warning-600" />
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-neutral-700">{slip.slip_ref ?? '—'}</td>
                    <td className="px-4 py-2.5 text-right amount font-medium text-neutral-900">
                      {formatAmount(Number(slip.total_amount), currency)}
                    </td>
                    <td className="px-4 py-2.5 text-neutral-900">{pickedByName ?? '—'}</td>
                    <td className="px-4 py-2.5">
                      <StatusBadge status={slip.status as DocumentStatus} />
                      {/* The entire point of the AP-review step is to weigh this reason —
                          without it AP is just clicking Approve/Reject blind. Truncated
                          inline, full text on hover/title since there's no room for a
                          multi-line reason in a table cell. */}
                      {slip.status === 'pending_ap_review' && slip.missing_slip_reason && (
                        <p
                          className="mt-1 max-w-[14rem] truncate text-xs text-neutral-500"
                          title={slip.missing_slip_reason}
                        >
                          {slip.missing_slip_reason}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <SlipAttachmentsCell agreementId={agreementId} slipId={slip.id} />
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        {canReview && (
                          <>
                            <Button
                              size="sm"
                              variant="success-outline"
                              onClick={() => handleReview(slip.id, 'approve')}
                              disabled={apReview.isPending}
                            >
                              <CheckCircle2 className="h-3.5 w-3.5" />
                              {rowApprovePending ? 'Working…' : 'Approve'}
                            </Button>
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => handleReview(slip.id, 'reject')}
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
                            onClick={() => handleVoid(slip.id, slip.slip_ref)}
                            disabled={voidSlip.isPending}
                            className={cn('text-danger-600 hover:text-danger-700')}
                          >
                            <Ban className="h-3.5 w-3.5" />
                            {rowVoidPending ? 'Working…' : 'Void'}
                          </Button>
                        )}
                        {!canReview && !canVoid && <span className="text-neutral-300">—</span>}
                      </div>
                    </td>
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
