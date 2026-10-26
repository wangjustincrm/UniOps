/**
 * Freight & Customs — on an NC raw-material / packaging PO, every arrival with
 * the freight / customs invoices linked to it, against what the PO's Incoterms
 * say is expected (Admin → Incoterms Settings).
 *
 * Read-only. Links are made from the fee invoice's own page (InvoiceFeeLinksPanel):
 * the invoice is the thing that arrives, and one freight bill or customs invoice
 * often covers several arrivals or several POs.
 */
import { Link } from 'react-router-dom'
import { Check, Minus, X, Loader2, Truck, ExternalLink } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { BackLink } from '@/components/BackLink'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { usePoFeeStatus } from '@/hooks/usePoFee'
import {
  FEE_STATUS_LABEL, FEE_TYPE_LABEL,
  type FeeStatus, type PoFeeInvoice,
} from '@/services/poFee'

export const FEE_STATUS_VARIANT: Record<FeeStatus, 'success' | 'danger' | 'neutral' | 'warning'> = {
  complete: 'success',
  missing: 'danger',
  no_arrivals: 'neutral',
  not_required: 'neutral',
  not_set: 'warning',
}

export function FeeStatusBadge({ status }: { status: FeeStatus }) {
  return (
    <Badge variant={FEE_STATUS_VARIANT[status]}>
      <span className="whitespace-nowrap">{FEE_STATUS_LABEL[status]}</span>
    </Badge>
  )
}

function InvoiceChip({ inv }: { inv: PoFeeInvoice }) {
  return (
    <Link
      to={`/invoices/${inv.invoice_id}`}
      title={`${inv.vendor_name} · ${inv.vendor_invoice_number}${inv.line_description ? ` · ${inv.line_description}` : ''}`}
      className="inline-flex items-center gap-1 rounded border border-neutral-200 bg-white px-1.5 py-0.5 font-mono text-xs text-primary-700 hover:border-primary-300 hover:bg-primary-50"
    >
      {inv.internal_ref}
      <span className="font-sans text-neutral-500">
        {formatAmount(Number(inv.line_total ?? inv.total_amount), inv.currency)}
      </span>
      <ExternalLink className="h-3 w-3 text-neutral-300" />
    </Link>
  )
}

function FeeCell({ needed, invoices }: { needed: boolean; invoices: PoFeeInvoice[] }) {
  if (invoices.length > 0) {
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        <Check className="h-4 w-4 shrink-0 text-success-600" aria-label="Received" />
        {invoices.map((i) => <InvoiceChip key={i.link_id} inv={i} />)}
      </div>
    )
  }
  if (!needed) return <Minus className="h-4 w-4 text-neutral-300" aria-label="Not required" />
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-danger-600">
      <X className="h-4 w-4" aria-hidden />
      Missing
    </span>
  )
}

export function PoFeePanel({ poId, canEditDetails }: { poId: string; canEditDetails: boolean }) {
  const { data, isLoading } = usePoFeeStatus(poId)
  if (isLoading) {
    return (
      <div className="flex items-center justify-center rounded-xl bg-white p-6 text-neutral-400 shadow-[0_1px_3px_rgba(10,124,124,0.08)]">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    )
  }
  if (!data || !data.applies) return null

  const { term } = data
  const needsFreight = !!term?.needs_freight
  const needsCustoms = !!term?.needs_customs
  const others = data.arrivals.flatMap((a) => a.other)
  const incoterms = [data.incoterms_code, data.incoterms_place].filter(Boolean).join(' ')

  return (
    <div className="flex flex-col gap-4 rounded-xl bg-white p-6 shadow-[0_1px_3px_rgba(10,124,124,0.08)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">
          <Truck className="h-4 w-4" />
          Freight &amp; Customs
        </h2>
        <FeeStatusBadge status={data.status} />
      </div>

      {term ? (
        <p className="text-sm text-neutral-600">
          Incoterms <span className="font-medium text-neutral-900">{incoterms}</span>
          {' — '}
          {needsFreight || needsCustoms
            ? `expects ${[needsFreight && 'a freight', needsCustoms && 'a customs'].filter(Boolean).join(' and ')} invoice for every arrival.`
            : 'no freight or customs invoice is expected.'}
        </p>
      ) : (
        <p className="rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-700">
          Incoterms is not set, so whether freight and customs invoices are expected cannot be judged.{' '}
          {canEditDetails && (
            <BackLink to={`/po/${poId}/edit-imported`} className="font-medium text-primary-700 underline">
              Set it in Edit Details
            </BackLink>
          )}
        </p>
      )}

      {data.arrivals.length === 0 ? (
        <p className="text-sm text-neutral-500">No arrivals yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-neutral-200">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500">
              <tr>
                <th className="px-3 py-2 text-left font-semibold">Arrival</th>
                <th className="px-3 py-2 text-left font-semibold">Date</th>
                <th className="px-3 py-2 text-left font-semibold">{FEE_TYPE_LABEL.freight}</th>
                <th className="px-3 py-2 text-left font-semibold">{FEE_TYPE_LABEL.customs}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {data.arrivals.map((a) => (
                <tr key={a.gr_id}>
                  <td className="px-3 py-2">
                    <Link to={`/gr/${a.gr_id}`} className="font-mono text-xs font-semibold text-neutral-700 hover:text-primary-700">
                      {a.gr_number}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-neutral-600">{formatDate(a.received_at)}</td>
                  <td className="px-3 py-2"><FeeCell needed={!!term && needsFreight} invoices={a.freight} /></td>
                  <td className="px-3 py-2"><FeeCell needed={!!term && needsCustoms} invoices={a.customs} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {others.length > 0 && (
        <div className={cn('flex flex-wrap items-center gap-1.5 text-sm text-neutral-600')}>
          <span>Other fees:</span>
          {others.map((i) => <InvoiceChip key={i.link_id} inv={i} />)}
        </div>
      )}

      {data.unassigned.length > 0 && (
        <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm">
          <p className="text-neutral-600">
            Linked before any arrival existed — open the invoice to choose its arrival:
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {data.unassigned.map((i) => (
              <span key={i.link_id} className="inline-flex items-center gap-1 text-xs text-neutral-500">
                {FEE_TYPE_LABEL[i.fee_type]} <InvoiceChip inv={i} />
              </span>
            ))}
          </div>
        </div>
      )}

      <p className="text-xs text-neutral-400">
        Freight and customs invoices are linked from the invoice&apos;s own page (Link to raw-material PO).
      </p>
    </div>
  )
}
