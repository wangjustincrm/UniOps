/** "Loblaws (AC-9898)" — a partner as text with its code (detail rows, titles); the code alone without a name. */
export function partnerLabel(name: string | null | undefined, code: string | null | undefined): string {
  // A partner whose name is its code (Shopify, Manual) is said once.
  return name ? `${name}${code && code.toLowerCase() !== name.toLowerCase() ? ` (${code})` : ''}` : (code ?? '')
}

/** An order's visible identifier: its Order ref (A.3, "LCL-4887314746"); the internal number before refs existed. */
export function orderRef(o: { source_reference?: string | null; order_number: number }): string {
  return o.source_reference || String(o.order_number)
}
