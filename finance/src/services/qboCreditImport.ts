/**
 * QBO vendor-credit import — a THROWAWAY migration client.
 *
 * QuickBooks is being decommissioned; delete this file, QboCreditImportPage and
 * its route once the balances are across. `financeApi` already prefixes
 * `/finance/v1` — do not repeat it here.
 *
 * Every money field is a Decimal serialised as a string by Pydantic. Type it
 * `string` and put arithmetic through Number().
 */
import { financeApi } from '@/lib/api'

export interface ImportCandidate {
  qbo_vendor_id: string
  qbo_display_name: string | null
  credit_count: number
  /** Decimal-as-string — run through Number() before arithmetic. */
  credit_total: string
  /** A vendor can hold credit in more than one currency, and a credit only
   * ever nets against a payment in its own currency. */
  currencies: string[]
  /** A PRE-FILL, never a decision — the backend imports only what the caller
   * explicitly maps. */
  suggested_vendor_id: string | null
  suggested_vendor_name: string | null
  already_imported: number
}

export interface ImportDriftRow {
  source_ref: string
  credit_number: string
  vendor_name: string
  /** Decimal-as-string. */
  imported_total: string
  /** Decimal-as-string. */
  qbo_balance: string
  /** Decimal-as-string. */
  applied_amount: string
}

export interface ImportCutover {
  sync_run_id: string | null
  finished_at: string | null
}

export interface ImportCandidatesResponse {
  candidates: ImportCandidate[]
  drift: ImportDriftRow[]
  cutover: ImportCutover
}

export interface ImportedRow {
  qbo_id: string
  vendor_credit_number: string
  vendor_name: string
  /** Decimal-as-string. */
  amount: string
  currency: string
}

export interface ImportDuplicateRow {
  qbo_id: string
  vendor_credit_number: string
  existing_credit_number: string
}

export interface ImportRunResponse {
  dry_run: boolean
  imported: number
  skipped_unmapped: number
  skipped_existing: number
  skipped_duplicate: number
  /** Decimal-as-string. */
  total_amount: string
  rows: ImportedRow[]
  duplicates: ImportDuplicateRow[]
}

export interface VendorOption {
  id: string
  code: string
  name: string
}

export const qboCreditImportApi = {
  candidates: () =>
    financeApi.get<ImportCandidatesResponse>('/qbo-credit-import/candidates'),
  vendors: (q?: string) =>
    financeApi.get<{ items: VendorOption[] }>(
      `/qbo-credit-import/vendors${q ? `?q=${encodeURIComponent(q)}` : ''}`,
    ),
  /** `dryRun` defaults to true on the server too — a forgotten flag must not
   * write. */
  run: (mapping: Record<string, string>, dryRun: boolean) =>
    financeApi.post<ImportRunResponse>('/qbo-credit-import/run', {
      mapping,
      dry_run: dryRun,
    }),
}
