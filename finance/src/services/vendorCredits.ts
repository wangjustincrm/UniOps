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
