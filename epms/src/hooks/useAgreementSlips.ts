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
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'slips'] }),
        queryClient.invalidateQueries({ queryKey: ['agreements'] }),
      ])
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to create slip'),
  })
}

export function useVoidSlip(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (slipId: string) => agreementSlipService.void(agreementId, slipId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'slips'] }),
        queryClient.invalidateQueries({ queryKey: ['agreements'] }),
      ])
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to void slip'),
  })
}

export function useApReviewSlip(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ slipId, action }: { slipId: string; action: SlipApReviewAction }) =>
      agreementSlipService.apReview(agreementId, slipId, action),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'slips'] }),
        queryClient.invalidateQueries({ queryKey: ['agreements'] }),
      ])
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to record AP review'),
  })
}
