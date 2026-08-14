import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { useAgreementSchedule } from '@/hooks/useAgreements'
import { useAssignBillingPeriod } from '@/hooks/useInvoices'
import type { ApiInvoice } from '@/services/invoices'
import type { ApiScheduleRow } from '@/services/agreement'

/**
 * The way out of "Invoice not linked to a billing period".
 *
 * A recurring invoice claims a scheduled period when it is matched. When the
 * automatic claim cannot place it — the amount falls outside the period's
 * tolerance, or the schedule has no candidate row — it lands in match_review
 * with no period, and approving it there never assigns one. From that point on
 * the invoice was unpayable and unfixable: /match refuses to run twice, and the
 * agreement's recurrence fields (tolerance included) are locked once it is
 * active. The only surface that offered the manual assignment was MatchPanel,
 * which is unreachable for an already-matched invoice.
 *
 * So this renders only in exactly that dead end — matched to a recurring
 * agreement, no period — and offers the same explicit assignment /match takes,
 * which deliberately skips the amount check: a person is overriding that
 * decision on purpose, which is the entire point of the escape hatch.
 */
export function InvoiceBillingPeriodPanel({ invoice }: { invoice: ApiInvoice }) {
  if (
    !invoice.agreement_id ||
    invoice.agreement_type !== 'recurring' ||
    invoice.schedule_id
  ) return null

  return <InvoiceBillingPeriodPanelBody invoice={invoice} agreementId={invoice.agreement_id} />
}

function InvoiceBillingPeriodPanelBody({
  invoice, agreementId,
}: { invoice: ApiInvoice; agreementId: string }) {
  const scheduleQuery = useAgreementSchedule(agreementId)
  const assign = useAssignBillingPeriod()
  const [selectedId, setSelectedId] = useState('')

  const rows: ApiScheduleRow[] = scheduleQuery.data?.items ?? []
  const openPeriods = rows
    .filter((r) => r.schedule_type === 'period' && r.invoice_id === null)
    .sort((a, b) => a.sequence - b.sequence)

  const invoiceTotal = Number(invoice.total_amount)

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-warning-200 bg-warning-50 p-4">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning-700" />
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium text-warning-800">Not linked to a billing period</p>
          {/* States the cause, because it is not visible anywhere else: the
              automatic claim compares the invoice total against the period's
              expected amount, and an agreement with no tolerance set requires
              them to be equal to the cent. */}
          <p className="text-xs text-warning-800">
            This invoice was matched to the agreement but never claimed a scheduled period —
            its total fell outside the period's tolerance, or no period was available at the
            time. A payment cannot be raised against it until one is assigned. Pick the period
            it pays for; the amount check is skipped because you are making that call
            deliberately.
          </p>
        </div>
      </div>

      {scheduleQuery.isLoading ? (
        <p className="rounded-lg border border-warning-200 bg-white px-3 py-4 text-center text-xs text-neutral-400">
          Loading the payment schedule…
        </p>
      ) : scheduleQuery.isError ? (
        // Never rendered as "no periods available": the schedule read is gated
        // on epms.agreement.read, which not every role that can match an
        // invoice holds, and an empty list would read as a schedule problem
        // rather than a permission one.
        <p className="rounded-lg border border-warning-200 bg-white px-3 py-2.5 text-xs text-warning-800">
          Couldn't load the payment schedule — you may not have permission to view it. Someone
          with access to the agreement can assign the period.
        </p>
      ) : openPeriods.length === 0 ? (
        <p className="rounded-lg border border-warning-200 bg-white px-3 py-2.5 text-xs text-warning-800">
          Every period on this agreement is already claimed by another invoice. The schedule may
          need extending before this invoice can be paid.
        </p>
      ) : (
        <>
          <div className="flex max-h-64 flex-col gap-1.5 overflow-y-auto">
            {openPeriods.map((row) => {
              const expected = row.expected_amount != null ? Number(row.expected_amount) : null
              return (
                <label
                  key={row.id}
                  className={cn(
                    'flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 transition-colors',
                    selectedId === row.id
                      ? 'border-primary-400 bg-primary-50'
                      : 'border-neutral-200 bg-white hover:bg-neutral-50',
                  )}
                >
                  <input
                    type="radio"
                    name="billing-period"
                    checked={selectedId === row.id}
                    onChange={() => setSelectedId(row.id)}
                    className="h-4 w-4 text-primary-600 focus:ring-primary-500"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-neutral-900">
                      {row.period_label ?? `Period #${row.sequence}`}
                      {row.status === 'overdue' && (
                        <span className="ml-1.5 text-xs font-medium text-warning-700">· overdue</span>
                      )}
                    </p>
                    <p className="text-[11px] text-neutral-500">
                      Expected {formatDate(row.expected_date ?? '')}
                      {expected !== null && <> · expected {formatAmount(expected, invoice.currency)}</>}
                    </p>
                  </div>
                  {/* The difference is shown, not hidden: assigning across a
                      gap this large is a decision worth seeing the size of. */}
                  {expected !== null && Math.round(expected * 100) !== Math.round(invoiceTotal * 100) && (
                    <span className="shrink-0 text-[11px] font-medium text-warning-700">
                      {formatAmount(invoiceTotal - expected, invoice.currency)} vs invoice
                    </span>
                  )}
                </label>
              )
            })}
          </div>
          <div className="flex justify-end">
            <Button
              size="sm"
              disabled={!selectedId || assign.isPending}
              onClick={() => assign.mutate({ id: invoice.id, schedule_id: selectedId })}
            >
              {assign.isPending ? 'Assigning…' : 'Assign Billing Period'}
            </Button>
          </div>
        </>
      )}

      {assign.error && (
        <p className="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          {assign.error instanceof Error ? assign.error.message : 'Could not assign the billing period'}
        </p>
      )}
    </div>
  )
}
