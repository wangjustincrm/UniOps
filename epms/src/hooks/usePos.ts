import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  poService,
  type PoFilters,
  type CreatePoBody,
  type UpdatePoBody,
  type PoActionBody,
  type PlaceOrderBody,
} from '@/services/po'
import { api } from '@/lib/api'

export interface PoAttachmentMeta {
  id: string
  filename: string
  content_type: string
  file_size: number
  created_at: string
  download_url: string | null
}

// Without explicit page/page_size the caller wants the complete list, so we
// page through the API (server defaults to 20 rows and silently truncates).
export function usePos(filters?: PoFilters) {
  const paged = filters?.page !== undefined || filters?.page_size !== undefined
  return useQuery({
    queryKey: ['pos', filters],
    queryFn: () => (paged ? poService.list(filters) : poService.listAll(filters)),
  })
}

export function usePo(id: string) {
  return useQuery({
    queryKey: ['pos', id],
    queryFn: () => poService.get(id),
    enabled: Boolean(id),
  })
}

export function usePoEvents(id: string) {
  return useQuery({
    queryKey: ['pos', id, 'events'],
    queryFn: () => poService.events(id),
    enabled: Boolean(id),
  })
}

export function usePoWorkflowSteps(id: string) {
  return useQuery({
    queryKey: ['po', id, 'workflow-steps'],
    queryFn: () => poService.workflowSteps(id),
    enabled: !!id,
  })
}

export function useCreatePo() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreatePoBody) => poService.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pos'] })
      queryClient.invalidateQueries({ queryKey: ['prs'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to create purchase order'),
  })
}

export function useUpdatePo() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdatePoBody }) =>
      poService.update(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['pos'] })
      queryClient.invalidateQueries({ queryKey: ['pos', id] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to update purchase order'),
  })
}

export function usePoAction(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: PoActionBody) => poService.action(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pos'] })
      queryClient.invalidateQueries({ queryKey: ['pos', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Purchase order action failed'),
  })
}

export function usePoAttachments(poId: string) {
  return useQuery<PoAttachmentMeta[]>({
    queryKey: ['pos', poId, 'attachments'],
    queryFn: () => api.get<PoAttachmentMeta[]>(`/po/${poId}/attachments`),
    enabled: Boolean(poId),
    staleTime: 30_000,
  })
}

export function usePlaceOrder(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: PlaceOrderBody) => poService.placeOrder(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pos'] })
      queryClient.invalidateQueries({ queryKey: ['pos', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to place order'),
  })
}
