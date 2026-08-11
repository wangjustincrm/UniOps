import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  agreementSlipService,
  type CreateSlipBody,
  type SlipApReviewAction,
  type SlipStatus,
} from '@/services/agreementSlips'

export function useAgreementSlips(agreementId: string, status?: SlipStatus) {
  return useQuery({
    queryKey: ['agreements', agreementId, 'slips', status],
    queryFn: () => agreementSlipService.list(agreementId, status ? { status } : undefined),
    enabled: Boolean(agreementId),
  })
}

export function useCreateSlip(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreateSlipBody) => agreementSlipService.create(agreementId, body),
    // await is load-bearing — see useConfirmPeriod/useAgreementAction in
    // hooks/useAgreements.ts for the same rationale: invalidateQueries only
    // *schedules* a background refetch, so without awaiting it isPending
    // flips false and the entry form re-arms while the slip list still shows
    // stale (pre-create) data.
    //
    // Scoped to THIS agreement's slip list only — no ['agreements'] (bare)
    // invalidate. That broad key used to prefix-match every agreement's data
    // app-wide (every OTHER open agreement's slips/attachments too, plus the
    // agreement list/header queries), and, within this agreement, every
    // per-row attachment query as collateral damage on every single mutation.
    // It was also redundant with the narrow invalidate right below it.
    // consumed_amount/NTE on the agreement header is derived from matched
    // INVOICES (crud/invoice.py, e.g. _recompute_consumed), never from
    // pickup slips directly — create/void/ap-review touch nothing on the
    // agreement row itself, so there is nothing on ['agreements'] for these
    // three mutations to invalidate in the first place.
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'slips'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to create slip'),
  })
}

export function useVoidSlip(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (slipId: string) => agreementSlipService.void(agreementId, slipId),
    // See useCreateSlip above for why this doesn't also invalidate ['agreements'].
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'slips'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to void slip'),
  })
}

export function useApReviewSlip(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ slipId, action }: { slipId: string; action: SlipApReviewAction }) =>
      agreementSlipService.apReview(agreementId, slipId, action),
    // See useCreateSlip above for why this doesn't also invalidate ['agreements'].
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'slips'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to record AP review'),
  })
}
