// Invoice-to-PO variance for the PA Detail approver view (Task 7 of
// 2026-08-13-invoice-match-visibility). Pure functions only — no rendering,
// no fetching. See InvoiceMatchVariancePanel.tsx for the component that
// consumes these.
//
// The brief's interface block names the invoice parameter type `Invoice`,
// but no such export exists in @/services/invoices — only `ApiInvoice`
// (confirmed: InvoiceReceiptsPanel.tsx and every other consumer import
// ApiInvoice). Using ApiInvoice here; functionally identical, naming only.
import type { ApiInvoice, InvoiceAllocation } from '@/services/invoices'
import type { ApiPoLineItem } from '@/services/po'
import { centsEqual } from '@/lib/money'

export type MatchMode = 'by-line' | 'by-amount'

// 'by-line': every allocation carries a real po_line_id — a line-to-line
// comparison is genuine information. 'by-amount' (total-value mode):
// po_line_id is null, matched against the PO's total — there is NO
// line-to-line correspondence, and nothing here may imply one. A mix (some
// allocations lined, some not) is treated as by-amount: a partially-fake
// line table is worse than an honest by-amount view.
export function matchMode(allocations: InvoiceAllocation[]): MatchMode {
  if (allocations.length === 0) return 'by-amount'
  return allocations.every((a) => a.po_line_id != null) ? 'by-line' : 'by-amount'
}

export interface LineComparison {
  allocationId: string
  invoiceLineDescription: string
  invoiceQty: number | null
  invoiceUnitPrice: number | null
  invoiceAmount: number
  poLineDescription: string | null
  poQty: number | null
  poUnitPrice: number | null
  poAmount: number | null
  variance: number
}

// Joins invoice line -> allocation -> PO line. Only meaningful to render as a
// line-to-line table when matchMode(invoice.allocations) === 'by-line' — the
// caller (InvoiceMatchVariancePanel) is responsible for not rendering a
// PO-line column when the mode is 'by-amount', even though this function
// will still happily return null poLine* fields for that case.
//
// Every numeric field arriving from the API can land as a Decimal-serialised
// JSON string (Pydantic), so every value is passed through Number() before
// any arithmetic — see matchVariance.test.ts's Decimal-as-string case.
export function buildLineComparisons(invoice: ApiInvoice, poLines: ApiPoLineItem[]): LineComparison[] {
  const allocations = invoice.allocations ?? []
  const lineItemById = new Map(
    (invoice.line_items ?? [])
      .filter((l): l is typeof l & { id: string } => !!l.id)
      .map((l) => [l.id, l] as const),
  )
  const poLineById = new Map(poLines.map((l) => [l.id, l] as const))

  return allocations.map((a) => {
    const invLine = lineItemById.get(a.invoice_line_id)
    const invoiceAmount = Number(a.allocated_amount)
    // po_line_id can be null (by-amount mode) or point at a line that isn't
    // in the poLines the caller passed (stale/removed line, or a different
    // PO) — both cases must yield null, never throw.
    const poLine = a.po_line_id ? poLineById.get(a.po_line_id) : undefined
    const poAmount = poLine ? Number(poLine.line_total) : null

    // Prefer the backend's own computed variance for this allocation — it is
    // real, server-verified data. Only fall back to local arithmetic when the
    // backend didn't supply one. When there is no PO amount to compare
    // against either, fall back to the invoice amount itself rather than
    // fabricating a zero/matched result.
    const backendVariance = a.variance != null ? Number(a.variance) : null
    const variance = backendVariance ?? (poAmount != null ? invoiceAmount - poAmount : invoiceAmount)

    return {
      allocationId: a.id,
      invoiceLineDescription: invLine?.description ?? '—',
      invoiceQty: invLine ? Number(invLine.quantity) : null,
      invoiceUnitPrice: invLine ? Number(invLine.unit_price) : null,
      invoiceAmount,
      poLineDescription: poLine ? poLine.description : (a.po_line_description ?? null),
      poQty: poLine ? Number(poLine.qty) : null,
      poUnitPrice: poLine ? Number(poLine.unit_price) : null,
      poAmount,
      variance,
    }
  })
}

export interface FeeLine {
  description: string
  amount: number
}

// Non-PO fee lines (non_po_fee === true — shipping, packaging, etc.) never
// receive an allocation at all: the backend enforces that a line cannot be
// both allocated to a PO and marked non-PO fee (crud/invoice.py's "mutual
// exclusion" check), so they never appear in buildLineComparisons' output.
//
// Both match modes subtract these lines' pre-tax total from invoice.amount
// before computing invoice.variance (crud/invoice.py:1101 — unconditional on
// po_line_id, so this is NOT a by-line-only concern), which means the header
// variance and the sum of the allocation rows legitimately disagree by
// exactly this total whenever the invoice carries a fee line. Neither number
// is wrong; the header is authoritative. The panel must render these lines
// (see InvoiceMatchVariancePanel's fee-line section) or a manager checking
// the arithmetic has no way to see why the rows don't sum to the headline.
export function feeLines(invoice: ApiInvoice): FeeLine[] {
  return (invoice.line_items ?? [])
    .filter((l) => l.non_po_fee === true)
    .map((l) => ({ description: l.description, amount: Number(l.line_total) }))
}

export function feeLineTotal(invoice: ApiInvoice): number {
  return feeLines(invoice).reduce((sum, l) => sum + l.amount, 0)
}

// House-account route (Task 8): compares an invoice's claimed receipts to the
// invoice total. Same convention as InvoiceReceiptsPanel.tsx's existing
// selection-preview logic (~lines 183-191) — that file is the source of truth
// this mirrors, not a second implementation of the idea:
//   ① Tax-INCLUSIVE — compares against invoice.total_amount, NOT invoice.amount.
//      This is the OPPOSITE of the PO route's pre-tax basis above, and that
//      asymmetry is correct: counter slips are tax-inclusive.
//   ② Only receipts that carry an amount participate — delivery notes and
//      service sign-offs (total_amount === null) are legitimately unpriced
//      and must not be treated as zero.
//   ③ Zero priced receipts claimed at all -> variance is 0 by definition, not
//      "the whole invoice". Otherwise a delivery-note-only invoice would
//      scream a full-invoice discrepancy that nobody created.
//   ④ centsEqual, never `!== 0` — plain float subtraction can land a hair off
//      zero when summing several receipt totals.
export function receiptSummary(invoice: ApiInvoice): {
  pricedCount: number
  receiptTotal: number
  variance: number
  hasVariance: boolean
} {
  const priced = (invoice.claimed_receipts ?? []).filter(
    (r) => r.total_amount !== null && r.total_amount !== undefined,
  )
  const receiptTotal = priced.reduce((sum, r) => sum + Number(r.total_amount), 0)
  const invoiceTotal = Number(invoice.total_amount)
  const hasPriced = priced.length > 0

  return {
    pricedCount: priced.length,
    receiptTotal,
    variance: hasPriced ? receiptTotal - invoiceTotal : 0,
    hasVariance: hasPriced && !centsEqual(receiptTotal, invoiceTotal),
  }
}
