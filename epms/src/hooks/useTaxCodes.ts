import { useQuery } from '@tanstack/react-query'
import { getTaxCodes, type TaxCode } from '@/services/invoiceTax'

/**
 * Active tax codes (mdm-api tax_codes, effective today) for PO/PA/invoice tax
 * pickers. Single source of truth — managed in Portal → Finance → Tax Settings.
 * Rates arrive as Decimal strings; Number()-coerce before arithmetic.
 */
export function useTaxCodes(): TaxCode[] {
  const { data } = useQuery({
    queryKey: ['tax-codes', 'active'],
    queryFn: getTaxCodes,
    staleTime: 60_000,
  })
  return data ?? []
}
