import { useState, type JSX } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { centsEqual } from '@/lib/money'
import { matchMode, buildLineComparisons, feeLines, feeLineTotal, receiptSummary } from '@/lib/matchVariance'
import { RECEIPT_TYPE_LABELS, type ReceiptType } from '@/services/agreementReceipts'
import type { ApiInvoice } from '@/services/invoices'
import type { ApiPoLineItem } from '@/services/po'

// PA Detail approver view of how each linked invoice compares to its PO —
// see docs/superpowers/specs/2026-08-13-ap-payment-officer-and-match-visibility-design.md.
// AP will not personally absorb a payment difference; instead of adding an
// approval step, the difference is surfaced here, to the manager who already
// approves the PA.
//
// Visual language deliberately mirrors InvoiceDetailPage.tsx's 3-way-match /
// PO-allocations blocks (~lines 1070-1190) rather than inventing a second
// look for the same concept.
//
// The by-line / by-amount split is the one rule this component must not
// break: by-amount (total-value) matches have no PO-line correspondence, so
// the by-amount branch below renders invoice line items and per-PO allocated
// amounts as two SEPARATE lists — never a joined table implying a pairing
// that was never made. See matchVariance.ts's matchMode/buildLineComparisons
// for the pure logic this renders.
//
// Fee-line reconciliation (fix round 1, finding 1): non-PO fee lines
// (shipping/packaging, `non_po_fee: true`) are excluded from the PO
// comparison entirely — they get no allocation — but their pre-tax total is
// still subtracted from invoice.amount when the backend computes the header
// variance (crud/invoice.py:1101, unconditional on po_line_id). So whenever
// an invoice carries a fee line, the header variance and the sum of the rows
// below it legitimately differ by that fee total. Both modes render a
// FeeLinesSection so a manager checking the arithmetic can see why, instead
// of being left to wonder whether the rows are wrong.
function FeeLinesSection({ invoice }: { invoice: ApiInvoice }) {
  const lines = feeLines(invoice)
  if (lines.length === 0) return null
  const total = feeLineTotal(invoice)

  return (
    <div className="rounded-lg border border-neutral-200 bg-neutral-50 overflow-hidden">
      <div className="px-3 py-2 border-b border-neutral-200">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-neutral-400">
          Not compared to PO (billed as invoiced)
        </h4>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[360px]">
          <tbody>
            {lines.map((line, idx) => (
              <tr key={idx} className="border-b border-neutral-200 last:border-0">
                <td className="px-3 py-2 text-xs text-neutral-500">{line.description}</td>
                <td className="px-3 py-2 text-right font-mono text-xs text-neutral-500">
                  {formatAmount(line.amount, invoice.currency)}
                </td>
              </tr>
            ))}
            <tr>
              <td className="px-3 py-2 text-xs font-medium text-neutral-600">Fee lines total</td>
              <td className="px-3 py-2 text-right font-mono text-xs font-medium text-neutral-600">
                {formatAmount(total, invoice.currency)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}

// House-account route (Task 8): compares the invoice to the receipts claimed
// against it, not to a PO — an agreement carries no PO/GR at all. The
// convention this must follow (tax-INCLUSIVE total_amount, unpriced receipts
// excluded, zero variance when nothing priced is claimed) is not invented
// here — it is InvoiceReceiptsPanel.tsx's existing selection-preview logic
// (~lines 183-191), lifted into matchVariance.ts's receiptSummary() so both
// call sites share one implementation.
//
// Renders NOTHING when there is no variance: unlike the PO-route panel
// above (which always shows a success-toned "0 variance" row once matched),
// this panel exists purely to flag something a manager needs to look at —
// silence here is the normal, healthy state, not a state worth confirming.
function HouseAccountReceiptPanel({ invoice }: { invoice: ApiInvoice }): JSX.Element | null {
  const [expanded, setExpanded] = useState(false)
  const summary = receiptSummary(invoice)

  if (!summary.hasVariance) return null

  const receipts = invoice.claimed_receipts ?? []

  return (
    <div className="rounded-xl border overflow-hidden border-danger-200 bg-danger-50">
      <div className="w-full flex items-center gap-2 pl-4 pr-2 py-2">
        <Link
          to={`/invoices/${invoice.id}`}
          className="font-mono text-xs text-primary-700 hover:underline shrink-0"
        >
          {invoice.internal_ref}
        </Link>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setExpanded((v) => !v)}
          className="flex-1 justify-start min-w-0 h-auto py-1.5 px-2 normal-case"
          aria-expanded={expanded}
          aria-label={expanded ? 'Collapse receipt variance detail' : 'Expand receipt variance detail'}
        >
          {expanded
            ? <ChevronDown className="h-4 w-4 shrink-0 text-neutral-400" />
            : <ChevronRight className="h-4 w-4 shrink-0 text-neutral-400" />}
          <span className="text-sm font-semibold truncate text-danger-700">
            · Receipts differ from invoice by {summary.variance >= 0 ? '+' : ''}{formatAmount(summary.variance, invoice.currency)}
          </span>
        </Button>
      </div>

      {expanded && (
        <div className="border-t border-inherit px-4 py-4 bg-white flex flex-col gap-4">
          <div className="overflow-x-auto rounded-lg border border-neutral-200">
            <table className="w-full text-sm min-w-[480px]">
              <thead>
                <tr className="border-b border-neutral-200 bg-neutral-50">
                  <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Reference</th>
                  <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Date</th>
                  <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Type</th>
                  <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Amount</th>
                </tr>
              </thead>
              <tbody>
                {receipts.map((r) => (
                  <tr key={r.id} className="border-b border-neutral-100 last:border-0">
                    <td className="px-3 py-2.5 font-mono text-xs text-neutral-800">{r.receipt_ref ?? '—'}</td>
                    <td className="px-3 py-2.5 text-xs text-neutral-600">{formatDate(r.receipt_date)}</td>
                    <td className="px-3 py-2.5 text-xs text-neutral-600">
                      {RECEIPT_TYPE_LABELS[r.receipt_type as ReceiptType] ?? r.receipt_type}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-xs">
                      {r.total_amount === null
                        // Delivery / service evidence carries no amount — shown, but
                        // visibly excluded from the total below (matches
                        // InvoiceReceiptsPanel's candidate-row convention).
                        ? <span className="text-neutral-400 italic">no amount</span>
                        : formatAmount(Number(r.total_amount), invoice.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3} className="px-3 py-2 text-xs font-medium text-neutral-600">
                    Priced receipts total ({summary.pricedCount} of {receipts.length})
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs font-medium text-neutral-600">
                    {formatAmount(summary.receiptTotal, invoice.currency)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// PO route (Task 7): compares the invoice against its linked PO's allocation
// rows. Extracted into its own component (rather than an inline branch of
// the exported shell below) so its hooks are never called conditionally —
// the shell picks exactly one of PoRouteVariancePanel / HouseAccountReceiptPanel
// / nothing per invoice, and each owns its own state.
function PoRouteVariancePanel({
  invoice, poLines, tolerancePct,
}: {
  invoice: ApiInvoice
  poLines: ApiPoLineItem[] | undefined
  tolerancePct: number
}): JSX.Element | null {
  const [expanded, setExpanded] = useState(false)

  const allocations = invoice.allocations ?? []
  // Nothing to compare — an invoice that was never matched against a PO
  // carries no allocations at all.
  if (allocations.length === 0) return null

  const variance = Number(invoice.variance ?? 0)
  const variancePct = Number(invoice.variance_pct ?? 0)
  const variancePctAbs = Math.abs(variancePct)

  const tone: 'success' | 'warning' | 'danger' = centsEqual(variance, 0)
    ? 'success'
    : variancePctAbs <= tolerancePct ? 'warning' : 'danger'

  const toneClasses: Record<typeof tone, { border: string; bg: string; text: string }> = {
    success: { border: 'border-success-200', bg: 'bg-success-50', text: 'text-success-700' },
    warning: { border: 'border-warning-200', bg: 'bg-warning-50', text: 'text-warning-700' },
    danger: { border: 'border-danger-200', bg: 'bg-danger-50', text: 'text-danger-700' },
  }
  const tc = toneClasses[tone]

  const mode = matchMode(allocations)
  const comparisons = mode === 'by-line' ? buildLineComparisons(invoice, poLines ?? []) : []
  const hasFeeLines = feeLines(invoice).length > 0

  return (
    <div className={cn('rounded-xl border overflow-hidden', tc.border, tc.bg)}>
      {/* Link and the toggle Button are SIBLINGS, not nested — a <button>'s
          content model forbids interactive descendants, so an <a> (Link)
          inside a Button is invalid HTML with ambiguous keyboard/focus
          semantics (fix round 1, finding 2). The Button is purely the
          expand/collapse toggle; it does not need to contain the link to be
          clickable across the row. */}
      <div className="w-full flex items-center gap-2 pl-4 pr-2 py-2">
        <Link
          to={`/invoices/${invoice.id}`}
          className="font-mono text-xs text-primary-700 hover:underline shrink-0"
        >
          {invoice.internal_ref}
        </Link>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setExpanded((v) => !v)}
          className="flex-1 justify-start min-w-0 h-auto py-1.5 px-2 normal-case"
          aria-expanded={expanded}
          aria-label={expanded ? 'Collapse invoice-to-PO variance detail' : 'Expand invoice-to-PO variance detail'}
        >
          {expanded
            ? <ChevronDown className="h-4 w-4 shrink-0 text-neutral-400" />
            : <ChevronRight className="h-4 w-4 shrink-0 text-neutral-400" />}
          <span className={cn('text-sm font-semibold truncate', tc.text)}>
            · Total variance {variance >= 0 ? '+' : ''}{formatAmount(variance, invoice.currency)}{' '}
            ({variancePct >= 0 ? '+' : ''}{variancePct.toFixed(2)}%)
          </span>
        </Button>
      </div>

      {expanded && (
        <div className="border-t border-inherit px-4 py-4 bg-white flex flex-col gap-4">
          {mode === 'by-line' ? (
            <>
              {/* Column label + caption (review finding, Task 8): this figure
                  comes straight off allocation.variance, computed server-side
                  (epms-api/app/crud/invoice.py:1050-1056) by summing
                  InvoicePoAllocation.allocated_amount for the PO LINE — with
                  no invoice_id filter. It is cumulative across every invoice
                  ever matched against that line, not this invoice's share of
                  it. The number is correct; only "Variance" as a bare label
                  would misread as "this invoice is short/over by X" when
                  partial invoicing across several invoices is a normal,
                  supported case here. Do not recompute a per-invoice figure —
                  there is no per-invoice reference in the data to compute it
                  from; label honestly instead. */}
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[640px]">
                  <thead>
                    <tr className="border-b border-neutral-200 bg-neutral-50">
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Invoice Line</th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">PO Line</th>
                      <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">PO line variance (all invoices)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {comparisons.map((row) => (
                      <tr key={row.allocationId} className="border-b border-neutral-100 last:border-0 align-top">
                        <td className="px-3 py-2.5">
                          <p className="text-xs font-medium text-neutral-800">{row.invoiceLineDescription}</p>
                          <p className="text-[11px] text-neutral-500">
                            {row.invoiceQty ?? '—'} × {row.invoiceUnitPrice != null ? formatAmount(row.invoiceUnitPrice, invoice.currency) : '—'}
                            {' = '}{formatAmount(row.invoiceAmount, invoice.currency)}
                          </p>
                        </td>
                        <td className="px-3 py-2.5">
                          {row.poLineDescription != null ? (
                            <>
                              <p className="text-xs font-medium text-neutral-800">{row.poLineDescription}</p>
                              <p className="text-[11px] text-neutral-500">
                                {row.poQty ?? '—'} × {row.poUnitPrice != null ? formatAmount(row.poUnitPrice, invoice.currency) : '—'}
                                {' = '}{row.poAmount != null ? formatAmount(row.poAmount, invoice.currency) : '—'}
                              </p>
                            </>
                          ) : (
                            <p className="text-xs text-neutral-400 italic">PO line not available</p>
                          )}
                        </td>
                        <td className={cn('px-3 py-2.5 text-right font-mono text-xs',
                          centsEqual(row.variance, 0) ? 'text-success-600' : 'text-danger-600')}>
                          {row.variance >= 0 ? '+' : ''}{formatAmount(row.variance, invoice.currency)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-[11px] text-neutral-400 italic">
                PO line variance figures are cumulative across every invoice matched to that PO line, not this invoice's share alone.
              </p>
              {hasFeeLines && <FeeLinesSection invoice={invoice} />}
            </>
          ) : (
            // by-amount: two independent lists, no join between them. Drawing
            // a line-to-line pairing here would fabricate data that was never
            // in the match — see the module docstring.
            <div className="flex flex-col gap-4">
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-2">Invoice Line Items</h4>
                <div className="overflow-x-auto rounded-lg border border-neutral-200">
                  <table className="w-full text-sm min-w-[480px]">
                    <thead>
                      <tr className="border-b border-neutral-200 bg-neutral-50">
                        <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Description</th>
                        <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Qty</th>
                        <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Unit Price</th>
                        <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {invoice.line_items.map((line, idx) => (
                        <tr key={line.id ?? idx} className="border-b border-neutral-100 last:border-0">
                          <td className="px-3 py-2.5 text-xs text-neutral-800">{line.description}</td>
                          <td className="px-3 py-2.5 text-right font-mono text-xs">{Number(line.quantity)}</td>
                          <td className="px-3 py-2.5 text-right font-mono text-xs">{formatAmount(Number(line.unit_price), invoice.currency)}</td>
                          <td className="px-3 py-2.5 text-right font-mono text-xs">{formatAmount(Number(line.line_total), invoice.currency)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-2">Amount Allocated per PO</h4>
                <div className="overflow-x-auto rounded-lg border border-neutral-200">
                  <table className="w-full text-sm min-w-[420px]">
                    <thead>
                      <tr className="border-b border-neutral-200 bg-neutral-50">
                        <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">PO</th>
                        <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Allocated</th>
                        <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Variance</th>
                      </tr>
                    </thead>
                    <tbody>
                      {allocations.map((a) => {
                        const allocVariance = a.variance != null ? Number(a.variance) : null
                        return (
                          <tr key={a.id} className="border-b border-neutral-100 last:border-0">
                            <td className="px-3 py-2.5">
                              <Link to={`/po/${a.po_id}`} className="font-mono text-xs text-primary-700 hover:underline">
                                {a.po_number ?? a.po_id.slice(0, 8)}
                              </Link>
                            </td>
                            <td className="px-3 py-2.5 text-right font-mono text-xs">
                              {formatAmount(Number(a.allocated_amount), invoice.currency)}
                            </td>
                            <td className={cn('px-3 py-2.5 text-right font-mono text-xs',
                              allocVariance == null || centsEqual(allocVariance, 0) ? 'text-success-600' : 'text-danger-600')}>
                              {allocVariance == null ? '—' : `${allocVariance >= 0 ? '+' : ''}${formatAmount(allocVariance, invoice.currency)}`}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {hasFeeLines && <FeeLinesSection invoice={invoice} />}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// Route dispatcher. Deliberately has no hooks of its own and no `useState` —
// each branch below is a self-contained component, so which one mounts for a
// given invoice can change (a re-match, or simply a different invoice in the
// linkedInvoices list PaDetailPage maps over) without violating the rules of
// hooks.
export function InvoiceMatchVariancePanel({
  invoice, poLines, tolerancePct,
}: {
  invoice: ApiInvoice
  poLines: ApiPoLineItem[] | undefined
  tolerancePct: number
}): JSX.Element | null {
  // house_account has no PO/GR — it compares against claimed receipts
  // instead (Task 8). recurring/milestone have neither a PO nor a receipt
  // comparison worth surfacing here yet — render nothing rather than
  // silently reusing PO-route logic against agreement data it was never
  // built to read.
  if (invoice.agreement_type === 'house_account') {
    return <HouseAccountReceiptPanel invoice={invoice} />
  }
  if (invoice.agreement_type === 'recurring' || invoice.agreement_type === 'milestone') {
    return null
  }
  return <PoRouteVariancePanel invoice={invoice} poLines={poLines} tolerancePct={tolerancePct} />
}
