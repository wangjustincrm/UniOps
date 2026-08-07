/**
 * Vendor credit netting preview.
 *
 * Read-only — `GET /vendor-credits/suggest` takes no locks and can be called
 * freely while an operator is reviewing a batch. It reports what the FIFO
 * default would net off a single payment application; it does not reserve
 * anything. The actual reservation happens inside
 * `POST /payments/batches/{id}/execute` (see PaymentBatchPage.tsx), which
 * accepts an optional `credit_ids_by_doc` override built from what the
 * operator deselected here.
 *
 * `financeApi` already prefixes `/finance/v1` — do not repeat it here.
 */
import { financeApi } from '@/lib/api'

export interface CreditSuggestion {
  credit_id: string
  credit_number: string
  credit_date: string
  /** Decimal serialized as a string by Pydantic — run through Number() before arithmetic. */
  remaining: string
  /** Decimal serialized as a string by Pydantic — run through Number() before arithmetic. */
  apply: string
}

export interface CreditSuggestResponse {
  /** Decimal serialized as a string by Pydantic — run through Number() before arithmetic. */
  gross: string
  suggested: CreditSuggestion[]
  /** Decimal serialized as a string by Pydantic — run through Number() before arithmetic. */
  credit_applied: string
  /** Decimal serialized as a string by Pydantic — run through Number() before arithmetic. */
  net: string
}

/** `docKind` must be 'pa' or 'pa_dir' — the backend 422s for anything else
 * (in particular 'expense_claim', which never takes vendor credits). Callers
 * must filter batch lines to those two kinds before calling this. */
export const suggestCredits = (docKind: string, docId: string) =>
  financeApi.get<CreditSuggestResponse>(
    `/vendor-credits/suggest?doc_kind=${docKind}&doc_id=${docId}`)

export interface PlannedApplication {
  credit: CreditSuggestion
  /** What the server would ACTUALLY apply to this credit given the current
   * selection — not `credit.apply`. */
  take: number
}

/**
 * Re-run the server's capped FIFO over only the credits still selected.
 *
 * `suggestion.apply` is NOT reusable once the operator deselects anything: it
 * was computed inside the FULL FIFO context, where a later credit had already
 * been squeezed by an earlier one. When the server receives an explicit id
 * list it re-runs capped FIFO over just those ids, which is a different
 * allocation.
 *
 * Concrete: gross 100, c1 remaining 60, c2 remaining 80 → preview says c1
 * apply 60, c2 apply 40. Deselect c1 and summing `apply` claims credits 40 /
 * net 60, while the server applies c2 = min(80, 100) = 80 → net 20, consuming
 * 80 of c2. Hence this loop, which mirrors
 * app/crud/vendor_credit.py:select_credits_for_payment exactly: oldest first
 * (the payload is already in that order), each take capped at the remaining
 * base, stop once the base is exhausted.
 *
 * Money fields are Decimal-as-string — Number() before every arithmetic op.
 */
export function planApplications(
  s: CreditSuggestResponse, off: Set<string>,
): PlannedApplication[] {
  const gross = Number(s.gross)
  const out: PlannedApplication[] = []
  let sum = 0
  for (const c of s.suggested) {
    if (off.has(c.credit_id)) continue
    const take = Math.min(Number(c.remaining), gross - sum)
    if (take <= 0) break
    sum += take
    out.push({ credit: c, take })
  }
  return out
}

export const plannedTotal = (plan: PlannedApplication[]): number =>
  plan.reduce((sum, p) => sum + p.take, 0)
