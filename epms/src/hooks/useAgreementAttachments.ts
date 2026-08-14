import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { agreementAttachmentService } from '@/services/agreementAttachments'

export function useAgreementAttachments(agreementId: string) {
  return useQuery({
    queryKey: ['agreement-attachments', agreementId],
    queryFn: () => agreementAttachmentService.list(agreementId),
    enabled: Boolean(agreementId),
  })
}

export function useUploadAgreementAttachment(agreementId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => agreementAttachmentService.upload(agreementId, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agreement-attachments', agreementId] }),
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to upload attachment'),
  })
}

export function useDeleteAgreementAttachment(agreementId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (attId: string) => agreementAttachmentService.delete(agreementId, attId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agreement-attachments', agreementId] }),
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to delete attachment'),
  })
}
