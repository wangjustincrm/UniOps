/** Default "Prepayment applied" on a Settlement / Balance PA (PP-012, PP-013).
 *
 *  The total prepaid on the PO, but never more than the final value being
 *  settled: the net payable may not be negative (PP-012), so filling in the
 *  whole prepaid total when it exceeds the final invoice gave a form that
 *  refused its own number (ISS-0237). Capped at the final value the net is 0
 *  and the server routes the overpaid settlement to Finance BP for
 *  confirmation (PP-013) — the variance it records is final value − total
 *  prepaid, not this amount. While the final value is not known yet (0), the
 *  prepaid total is shown. */
export function defaultPrepaymentApplied(prepaidTotal: number, finalTotal: number): number {
  if (finalTotal <= 0) return prepaidTotal
  return Math.min(prepaidTotal, finalTotal)
}

/** Prepaid more than the final value — a credit note is expected (PP-013). */
export function isSettlementOverpaid(prepaidTotal: number, finalTotal: number): boolean {
  return finalTotal > 0 && prepaidTotal - finalTotal > 0.01
}
