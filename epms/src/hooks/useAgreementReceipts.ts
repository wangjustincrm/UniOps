import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  agreementReceiptService,
  type CreateReceiptBody,
  type ReceiptApReviewAction,
  type ReceiptStatus,
} from '@/services/agreementReceipts'

export function useAgreementReceipts(agreementId: string, status?: ReceiptStatus) {
  return useQuery({
    queryKey: ['agreements', agreementId, 'receipts', status],
    queryFn: () => agreementReceiptService.list(agreementId, status ? { status } : undefined),
    enabled: Boolean(agreementId),
  })
}

export function useCreateReceipt(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreateReceiptBody) => agreementReceiptService.create(agreementId, body),
    // await is load-bearing — see useConfirmPeriod/useAgreementAction in
    // hooks/useAgreements.ts for the same rationale: invalidateQueries only
    // *schedules* a background refetch, so without awaiting it isPending
    // flips false and the entry form re-arms while the receipt list still shows
    // stale (pre-create) data.
    //
    // Scoped to THIS agreement's receipt list only — no ['agreements'] (bare)
    // invalidate. That broad key used to prefix-match every agreement's data
    // app-wide (every OTHER open agreement's receipts/attachments too, plus the
    // agreement list/header queries), and, within this agreement, every
    // per-row attachment query as collateral damage on every single mutation.
    // It was also redundant with the narrow invalidate right below it.
    // consumed_amount/NTE on the agreement header is derived from matched
    // INVOICES (crud/invoice.py, e.g. _recompute_consumed), never from
    // pickup receipts directly — create/void/ap-review touch nothing on the
    // agreement row itself, so there is nothing on ['agreements'] for these
    // three mutations to invalidate in the first place.
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'receipts'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to create receipt'),
  })
}

export function useVoidReceipt(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (receiptId: string) => agreementReceiptService.void(agreementId, receiptId),
    // See useCreateReceipt above for why this doesn't also invalidate ['agreements'].
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'receipts'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to void receipt'),
  })
}

export function useApReviewReceipt(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ receiptId, action }: { receiptId: string; action: ReceiptApReviewAction }) =>
      agreementReceiptService.apReview(agreementId, receiptId, action),
    // See useCreateReceipt above for why this doesn't also invalidate ['agreements'].
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'receipts'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to record AP review'),
  })
}
