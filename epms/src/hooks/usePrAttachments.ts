import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { prAttachmentService } from '@/services/prAttachments'

export function usePrAttachments(prId: string) {
  return useQuery({
    queryKey: ['pr-attachments', prId],
    queryFn: () => prAttachmentService.list(prId),
    enabled: Boolean(prId),
  })
}

export function useUploadAttachment(prId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => prAttachmentService.upload(prId, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pr-attachments', prId] }),
  })
}

export function useDeleteAttachment(prId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (attId: string) => prAttachmentService.delete(prId, attId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pr-attachments', prId] }),
  })
}
