type ClaimingPa = {
  pa_number: string
  pa_type?: string | null
  status: string
  invoice_ids: string[]
}

/** Invoice id → the PA number that already claims it, among the PAs of one PO
 *  or agreement (ISS-0112). A settlement or balance PA pays the rest of an
 *  invoice its PO's prepayment paid part of, so prepayments claim nothing from
 *  it: the same exception as the server's _assert_invoices_not_on_other_live_pa. */
export function invoiceClaims(pas: ClaimingPa[], forPaType?: string | null): Map<string, string> {
  const settling = forPaType === 'settlement' || forPaType === 'balance'
  const claims = new Map<string, string>()
  for (const pa of pas) {
    if (['cancelled', 'rejected'].includes(pa.status)) continue
    if (settling && pa.pa_type === 'prepayment') continue
    for (const invId of pa.invoice_ids) {
      if (!claims.has(invId)) claims.set(invId, pa.pa_number)
    }
  }
  return claims
}

/** Sum of PO line totals. The API sends money as strings, so `+` alone would
 *  concatenate them. */
export function poLinesSubtotal(lines: { line_total: number | string }[]): number {
  return lines.reduce((sum, l) => sum + Number(l.line_total), 0)
}
