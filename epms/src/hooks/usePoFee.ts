import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { poFeeService, type FeeLinkBody, type FeeType } from '@/services/poFee'

export function usePoFeeStatus(poId: string, enabled = true) {
  return useQuery({
    queryKey: ['po-fee', poId],
    queryFn: () => poFeeService.poFeeStatus(poId),
    enabled: Boolean(poId) && enabled,
  })
}

export function useInvoiceFeeLinks(invoiceId: string) {
  return useQuery({
    queryKey: ['invoice-fee-links', invoiceId],
    queryFn: () => poFeeService.invoiceLinks(invoiceId),
    enabled: Boolean(invoiceId),
  })
}

export function useSaveInvoiceFeeLinks(invoiceId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (links: FeeLinkBody[]) => poFeeService.saveInvoiceLinks(invoiceId, links),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['invoice-fee-links', invoiceId] })
      // Every PO the links touched (or used to) re-rates: its panel and the
      // list's Fees column.
      qc.invalidateQueries({ queryKey: ['po-fee'] })
      qc.invalidateQueries({ queryKey: ['pos'] })
    },
  })
}

export function useFeeLinkCandidates(q: string, limit: number, feeType: FeeType, enabled = true) {
  return useQuery({
    queryKey: ['fee-link-candidates', q, limit, feeType],
    queryFn: () => poFeeService.candidates(q, limit, feeType),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
}
