import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { paAttachmentService } from '@/services/paAttachments'

export function usePaAttachments(paId: string) {
  return useQuery({
    queryKey: ['pa-attachments', paId],
    queryFn: () => paAttachmentService.list(paId),
    enabled: Boolean(paId),
    staleTime: 60_000,
  })
}

export function useUploadPaAttachment(paId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => paAttachmentService.upload(paId, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pa-attachments', paId] }),
  })
}

export function useDeletePaAttachment(paId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (attId: string) => paAttachmentService.delete(paId, attId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pa-attachments', paId] }),
  })
}
