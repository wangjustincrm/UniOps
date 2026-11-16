import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { agreementAttachmentService } from '@/services/agreementAttachments'

export function useAgreementAttachments(agreementId: string) {
  return useQuery({
    queryKey: ['agreement-attachments', agreementId],
    queryFn: () => agreementAttachmentService.list(agreementId),
    enabled: Boolean(agreementId),
  })
}

// No onError alert: AgreementDetailPage shows a refusal under its Attachments
// heading (mutation.error).
export function useUploadAgreementAttachment(agreementId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => agreementAttachmentService.upload(agreementId, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agreement-attachments', agreementId] }),
  })
}

export function useDeleteAgreementAttachment(agreementId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (attId: string) => agreementAttachmentService.delete(agreementId, attId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agreement-attachments', agreementId] }),
  })
}
