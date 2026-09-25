import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  creditAttachmentService, vendorCreditsService, type CreateVendorCreditBody,
} from '@/services/vendorCredits'

export function useVendorCredits(status?: string) {
  return useQuery({
    queryKey: ['vendor-credits', status ?? 'all'],
    queryFn: () => vendorCreditsService.list(status),
    staleTime: 30_000,
  })
}

export function useCreateVendorCredit() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateVendorCreditBody) => vendorCreditsService.create(body),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['vendor-credits'] }) },
  })
}

type ReviewAction =
  | { id: string; action: 'approve'; note?: string }
  | { id: string; action: 'reject' | 'void'; note: string }

export function useReviewVendorCredit() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: ReviewAction) => {
      if (v.action === 'approve') return vendorCreditsService.approve(v.id, v.note)
      if (v.action === 'reject')  return vendorCreditsService.reject(v.id, v.note)
      return vendorCreditsService.void(v.id, v.note)
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['vendor-credits'] }) },
  })
}

export const creditAttachmentsKey = (creditId: string) => ['vendor-credit-attachments', creditId]

export function useCreditAttachments(creditId: string | null) {
  return useQuery({
    queryKey: creditAttachmentsKey(creditId ?? ''),
    queryFn: () => creditAttachmentService.list(creditId as string),
    enabled: !!creditId,
  })
}

export function useUploadCreditAttachment(creditId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => creditAttachmentService.upload(creditId, file),
    onSettled: () => { void qc.invalidateQueries({ queryKey: creditAttachmentsKey(creditId) }) },
  })
}
