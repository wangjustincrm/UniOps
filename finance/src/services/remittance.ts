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

export type RemittanceScope = { kind: 'batch' | 'payment'; id: string }

export type PayeeGroupLine = {
  /** Vendor invoice number for vendor payees, claim number for employees. */
  reference: string
  payment_date: string
  /** Decimal serialized as a string by Pydantic — run through Number() before arithmetic. */
  amount: string
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

function base(scope: RemittanceScope): string {
  return scope.kind === 'batch'
    ? `/payments/batches/${scope.id}/remittance`
    : `/payments/${scope.id}/remittance`
}

export function fetchPreview(scope: RemittanceScope): Promise<RemittancePreview> {
  return financeApi.get<RemittancePreview>(`${base(scope)}/preview`)
}

/**
 * `recipients: null` sends every non-blocked payee found by the preview; a
 * list narrows which groups are attempted. Narrowing only — the server
 * refuses blocked payees regardless of what is requested here.
 *
 * `resend` defaults to false: a payee whose log row is already `sent` for
 * the effective scope (batch or payment — see the backend's cross-scope
 * lookup) is refused server-side and comes back `skipped`, not re-emailed.
 * Pass `resend: true` only when the operator has deliberately re-checked a
 * payee the panel already shows as sent.
 */
export function sendRemittance(
  scope: RemittanceScope,
  recipients: { recipient_kind: string; party_id: string }[] | null,
  resend = false,
): Promise<SendResult> {
  return financeApi.post<SendResult>(`${base(scope)}/send`, { recipients, resend })
}
