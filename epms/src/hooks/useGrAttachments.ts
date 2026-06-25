import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { grAttachmentService } from '@/services/grAttachments'

export function useGrAttachments(grId: string) {
  return useQuery({
    queryKey: ['gr-attachments', grId],
    queryFn: () => grAttachmentService.list(grId),
    enabled: Boolean(grId),
    staleTime: 60_000,
  })
}

export function useUploadGrAttachment(grId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => grAttachmentService.upload(grId, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['gr-attachments', grId] }),
  })
}

export function useDeleteGrAttachment(grId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (attId: string) => grAttachmentService.delete(grId, attId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['gr-attachments', grId] }),
  })
}
