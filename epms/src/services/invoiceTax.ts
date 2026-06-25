/** Invoice line-level tax split (Phase a A2) — epms-api /invoices/{id}/tax-lines
 *  + tax code master from mdm-api (B2 tax engine). */
import { api, mdmApi } from '@/lib/api'

export interface TaxLine {
  id?: string
  line_no?: number
  tax_code: string
  tax_amount: string          // Decimal arrives as string — Number() before math
  taxable_amount?: string | null
  recoverable: boolean
}

export interface TaxLinesResponse {
  invoice_id: string
  tax_amount: string          // derived header value (Σ lines)
  total_amount: string
  lines: TaxLine[]
}

export interface TaxCode {
  code: string
  name: string
  tax_type: string
  province: string | null
  rate: string
  recoverable: boolean
}

export const getTaxLines = (invoiceId: string) =>
  api.get<TaxLinesResponse>(`/invoices/${invoiceId}/tax-lines`)

export const saveTaxLines = (invoiceId: string, lines: TaxLine[]) =>
  api.put<TaxLinesResponse>(`/invoices/${invoiceId}/tax-lines`,
    lines.map(({ tax_code, tax_amount, taxable_amount, recoverable }) => ({
      tax_code, tax_amount, taxable_amount: taxable_amount ?? null, recoverable,
    })))

export const getTaxCodes = () => mdmApi.get<TaxCode[]>('/tax/codes')
