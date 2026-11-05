/**
 * What NC holds about an open payable beyond its own documents (finance-api
 * services/nc_ap_bill_evidence.py):
 *
 * - a journal voucher that cleared it — old bills with no payment document,
 *   which finance settled with a GL voucher naming the invoice in its summary;
 * - the same invoice payable in another currency — a supply-chain CAD bill
 *   repeating a foreign-currency bill finance keyed and paid.
 *
 * Both are evidence for finance to judge, never netted off the balance.
 */

export interface JvMatch {
  jv_number: string; voucher_date: string | null; subsystem: string | null
  currency: string | null
  /** What the line did to the payable: positive reduces it. */
  amount: string
  /** The invoice number found in the summary. */
  invoice_no: string
  summary: string | null
  /** The line's amount equals the bill's open balance. */
  amount_matches: boolean
}

export interface OtherCurrencyBill {
  bill_no: string; currency: string; bill_date: string | null
  money_cr: string; money_bal: string
  /** The invoice numbers the two bills share. */
  invoice_nos: string[]
}

function money(v: string) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function BillEvidence({ jv, other }: { jv?: JvMatch[]; other?: OtherCurrencyBill[] }) {
  if (!jv?.length && !other?.length) return null
  return (
    <div className="mt-1 space-y-0.5 font-sans text-[10px] leading-snug">
      {jv?.map((m, k) => (
        <div key={`jv${k}`}
             title={[m.summary, m.subsystem && `source: ${m.subsystem}`].filter(Boolean).join(' · ')}
             className={m.amount_matches ? 'text-emerald-700' : 'text-sky-700'}>
          <span className="rounded bg-sky-50 px-1 py-px font-semibold">In JV</span>{' '}
          <span className="font-mono">{m.jv_number}</span> · {m.voucher_date ?? '—'} ·{' '}
          {money(m.amount)} {m.currency} · inv {m.invoice_no}
          {m.amount_matches && <span className="font-semibold"> · amount matches</span>}
        </div>
      ))}
      {other?.map((o) => (
        <div key={o.bill_no} className="text-amber-700"
             title={`Invoice ${o.invoice_nos.join(', ')} is also on ${o.bill_no} (${o.currency}), billed ${money(o.money_cr)}`}>
          <span className="rounded bg-amber-50 px-1 py-px font-semibold">Also in {o.currency}</span>{' '}
          <span className="font-mono">{o.bill_no}</span> · {o.bill_date ?? '—'} · inv {o.invoice_nos.join(', ')} ·{' '}
          {Number(o.money_bal) === 0 ? 'settled' : `open ${money(o.money_bal)}`}
        </div>
      ))}
    </div>
  )
}
