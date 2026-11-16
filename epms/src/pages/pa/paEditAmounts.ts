/** Pre-tax and Tax that Edit PA fills in once the invoice or PO-line
 *  selection has changed (PRD §3.15.2 charge breakdown, same as New PA):
 *  pre-tax from the PA's derived lines or the ticked invoices, else from the
 *  ticked PO lines; tax from the invoices, else pre-tax × the PO's rate (CAD
 *  only). Nothing ticked → both empty, so the total reads "—" (ISS-0261).
 *  `tax` is what to show when the user has not typed Tax by hand. */
export function paEditAutoAmounts(i: {
  invoiceCount: number
  derivedTotal: number | null
  invoiceSubtotal: number
  invoiceTax: number
  poLinesSubtotal: number
  isAgreementMode: boolean
  currency: string | null | undefined
  taxRate: number
}): { subtotal: string; tax: string } {
  const linked = i.invoiceCount > 0
  const auto = linked ? (i.derivedTotal ?? i.invoiceSubtotal) : (i.isAgreementMode ? 0 : i.poLinesSubtotal)
  const pretax = auto > 0 ? Number(auto.toFixed(2)) : 0
  const subtotal = pretax > 0 ? String(pretax) : ''
  if (linked) return { subtotal, tax: i.invoiceTax.toFixed(2) }
  if (!i.isAgreementMode && i.currency === 'CAD' && pretax > 0) {
    return { subtotal, tax: (pretax * i.taxRate).toFixed(2) }
  }
  return { subtotal, tax: '' }
}
