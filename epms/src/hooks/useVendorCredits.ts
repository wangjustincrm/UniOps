import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  creditAttachmentService, vendorCreditsService, type CreateVendorCreditBody,
  type VendorCredit, type VendorCreditListOptions,
} from '@/services/vendorCredits'

export function useVendorCredits(status?: string, opts?: VendorCreditListOptions) {
  return useQuery({
    // Page and sort are in the key; mutations invalidate the ['vendor-credits'] prefix.
    queryKey: ['vendor-credits', status ?? 'all', opts ?? null],
    queryFn: () => vendorCreditsService.list(status, opts),
    staleTime: 30_000,
  })
}

/** One credit by id (GET /vendor-credits/{id}) — for a credit that need not be
 * on the list page currently shown. `seed` (e.g. the create response) shows
 * at once; it is refetched immediately and after every vendor-credit mutation
 * (same ['vendor-credits'] prefix). */
export function useVendorCredit(id: string | null, seed?: VendorCredit | null) {
  return useQuery({
    queryKey: ['vendor-credits', 'detail', id],
    queryFn: () => vendorCreditsService.get(id!),
    enabled: !!id,
    initialData: seed && seed.id === id ? seed : undefined,
    initialDataUpdatedAt: 0,
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
