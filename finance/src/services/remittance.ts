/**
 * Remittance advice API client.
 *
 * Two scopes share one implementation on the backend (see
 * finance-api/app/api/v1/remittance.py): a `batch` scope is every completed
 * payment record sharing a batch_id, a `payment` scope is one record. Both
 * are mounted on the payments router, so paths below sit under `/payments`.
 * `financeApi` already prefixes `/finance/v1` — do not repeat it here.
 *
 * Preview is computed live on every call — it is never cached server-side,
 * so a vendor email filled in after the payment, or an invoice number
 * attached later, shows up on the very next fetch. `fetchPreview` is safe to
 * call as a plain "Refresh" — there is nothing to bust.
 */
import { financeApi } from '@/lib/api'

export type RemittanceScope =
  | { kind: 'batch'; id: string }
  | { kind: 'payment'; id: string }
  /** An ad-hoc set of payments, not tied to a batch — see
   * finance-api/app/api/v1/remittance.py's selection endpoints. All
   * payments must resolve to one payee; the backend rejects a selection
   * spanning more than one with a 400 (the UI should keep the operator from
   * ever hitting that). */
  | { kind: 'selection'; paymentIds: string[] }

/**
 * Stable identity for a scope, safe to use as a react-query key / effect
 * dependency. For 'batch'/'payment' this is just `kind:id`. For 'selection'
 * it mirrors the backend's deterministic scope-id derivation
 * (`rem.selection_scope_id` — sort + dedupe the ids) so that reopening the
 * exact same set of payments — regardless of the order they were selected
 * in — produces the same key and reuses the cached preview/send state
 * instead of treating it as a brand new scope.
 */
export function scopeKey(scope: RemittanceScope): string {
  if (scope.kind === 'selection') {
    return `selection:${[...new Set(scope.paymentIds)].sort().join(',')}`
  }
  return `${scope.kind}:${scope.id}`
}

export type AppliedCreditNote = {
  /** The vendor's own credit-note number — never our internal credit_number. */
  vendor_credit_number: string
  /** Decimal serialized as a string by Pydantic — run through Number() before arithmetic. */
  applied_amount: string
}

export type PayeeGroupLine = {
  /** Vendor invoice number for vendor payees, claim number for employees. */
  reference: string
  payment_date: string
  /** Decimal serialized as a string by Pydantic — run through Number() before arithmetic. */
  amount: string
  /** Invoice/claim amount before any vendor credit was netted off — same
   * figure as `amount` when nothing was netted. Decimal-as-string. */
  gross: string
  /** Total vendor credit netted off this line. "0.00" when none — the shape
   * is uniform across every line, not conditional. Decimal-as-string. */
  credit_applied: string
  /** One entry per vendor credit note netted off this line, each naming the
   * vendor's own document number — empty when `credit_applied` is "0.00". */
  credit_notes: AppliedCreditNote[]
}

export type LastSend = {
  status: string
  error: string | null
  attempts: number
  sent_at: string | null
}

export type PayeeGroup = {
  recipient_kind: 'vendor' | 'employee'
  party_id: string
  party_name: string
  email: string
  currency: string
  /** Decimal serialized as a string by Pydantic — run through Number() before arithmetic. */
  total: string
  /** e.g. "missing_email", "missing_invoice_no" — raw keys, not display text. */
  block_reasons: string[]
  lines: PayeeGroupLine[]
  /** Most recent send attempt logged for this payee under this scope, if any. */
  last_send: LastSend | null
}

export type RemittancePreview = {
  /** false when remittance email is not configured / switched off in Company Settings —
   *  in that case `groups` should not be read as "nobody to email". */
  enabled: boolean
  reference: string
  payment_method: string
  groups: PayeeGroup[]
}

export type SendResultItem = {
  recipient_kind: string
  party_id: string
  party_name: string
  status: string
  /**
   * Present for failed and skipped results. One backend subtlety: when the
   * email actually sent but writing its log row failed, status is still
   * "failed" but `error` begins with "email sent but not logged" — the UI
   * must show this text, not just the bare status, or an operator will
   * resend and double the vendor's remittance email.
   */
  error: string | null
}

export type SendResult = {
  sent: number
  failed: number
  skipped: number
  results: SendResultItem[]
}

/** Path base for the batch/payment scopes only — 'selection' has no path
 * segment of its own (the id list travels in the body instead), so it is
 * branched on separately in `fetchPreview`/`sendRemittance` below. */
function base(scope: { kind: 'batch' | 'payment'; id: string }): string {
  return scope.kind === 'batch'
    ? `/payments/batches/${scope.id}/remittance`
    : `/payments/${scope.id}/remittance`
}

export function fetchPreview(scope: RemittanceScope): Promise<RemittancePreview> {
  if (scope.kind === 'selection') {
    return financeApi.post<RemittancePreview>(
      '/payments/remittance/selection/preview', { payment_ids: scope.paymentIds })
  }
  return financeApi.get<RemittancePreview>(`${base(scope)}/preview`)
}

/**
 * `recipients: null` sends every non-blocked payee found by the preview; a
 * list narrows which groups are attempted. Narrowing only — the server
 * refuses blocked payees regardless of what is requested here.
 *
 * `resend` lives on each recipient individually, not as a single flag over
 * the whole request (Round 2 fix — see RemittancePanel.tsx's `handleSend`).
 * A recipient's `resend` defaults to false: a payee whose log row is
 * already `sent` for the effective scope (batch or payment — see the
 * backend's cross-scope lookup) is refused server-side and comes back
 * `skipped`, not re-emailed. Set `resend: true` on a recipient only when the
 * operator has deliberately re-checked that specific payee from a panel row
 * already showing it as sent — one recipient's resend must never be folded
 * into a blanket flag that also waives the guard for every OTHER recipient
 * in the same request.
 */
export function sendRemittance(
  scope: RemittanceScope,
  recipients: { recipient_kind: string; party_id: string; resend?: boolean }[] | null,
  /**
   * `YYYY-MM-DD` — the date the advice tells the payee the funds left, for
   * every line in it. AP often sends the advice a day or two after the money
   * actually moved, so the date recorded against the payment is not always
   * the date the payee's bank will show. Omitted (or undefined), each line
   * keeps its own recorded payment date, exactly as before this existed.
   *
   * Display only: it changes what the email says, never the payment record
   * behind the GL. The server rejects a future date with a 400.
   */
  paymentDate?: string,
): Promise<SendResult> {
  const payment_date = paymentDate || undefined
  if (scope.kind === 'selection') {
    return financeApi.post<SendResult>(
      '/payments/remittance/selection/send',
      { payment_ids: scope.paymentIds, recipients, payment_date })
  }
  return financeApi.post<SendResult>(`${base(scope)}/send`, { recipients, payment_date })
}
