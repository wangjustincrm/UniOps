/**
 * The supplier invoices behind one NC payable (finance-api
 * services/nc_ap_bill_invoices.py).
 *
 * An NC bill carries one invoice per LINE, often several — a freight bill can
 * be seven invoices. Showing one number beside the bill's total reads as "this
 * invoice is the whole amount", so a bill with several invoices lists each one
 * with its own amount; those amounts add up to the row's total.
 */

export interface BillInvoice {
  invoice_no: string | null
  purchase_orders: string[]
  summary: string | null
  /** NC lines carrying this number (one invoice can be split over lines). */
  lines: number
  money_cr: string
  money_bal: string
}

function money(v: string) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** Every PO across the bill's invoices, once each, in NC's line order. */
export function billPurchaseOrders(invoices: BillInvoice[]): string[] {
  return [...new Set(invoices.flatMap((i) => i.purchase_orders))]
}

/** For CSV: "3417820 (395.50); 3418010 (452.00)" — the amount only when there
 *  is more than one invoice, so a single-invoice bill still exports a bare number. */
export function invoicesCsv(invoices: BillInvoice[], amount: 'money_cr' | 'money_bal' = 'money_bal') {
  if (invoices.length <= 1) return invoices[0]?.invoice_no ?? ''
  return invoices.map((i) => `${i.invoice_no ?? '(no number)'} (${i[amount]})`).join('; ')
}

/** Invoice-number cell. `amount` picks the figure shown beside each invoice —
 *  the one the row's own amount column is about. */
export function BillInvoices({ invoices, amount = 'money_bal' }: {
  invoices: BillInvoice[]
  amount?: 'money_cr' | 'money_bal'
}) {
  if (invoices.length === 0) return <span className="text-neutral-400">—</span>
  if (invoices.length === 1) {
    const i = invoices[0]
    return <span title={i.summary ?? undefined}>{i.invoice_no ?? '—'}</span>
  }
  return (
    <div className="min-w-[11rem]">
      <div className="mb-0.5 font-sans text-[10px] uppercase tracking-wide text-neutral-400">
        {invoices.length} invoices
      </div>
      <ul className="space-y-0.5">
        {invoices.map((i, k) => (
          <li key={k} className="flex justify-between gap-3" title={i.summary ?? undefined}>
            <span className={i.invoice_no ? undefined : 'italic text-neutral-400'}>
              {i.invoice_no ?? 'no number'}
            </span>
            <span className="tabular-nums text-neutral-500">{money(i[amount])}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
