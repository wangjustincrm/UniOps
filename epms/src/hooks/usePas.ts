import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  paService,
  type PaFilters,
  type CreatePaBody,
  type UpdatePaBody,
  type PaActionBody,
} from '@/services/pa'

// Without explicit page/page_size the caller wants the complete list, so we
// page through the API (server defaults to 20 rows and silently truncates).
export function usePas(filters?: PaFilters, enabled = true) {
  const paged = filters?.page !== undefined || filters?.page_size !== undefined
  return useQuery({
    queryKey: ['pas', filters],
    queryFn: () => (paged ? paService.list(filters) : paService.listAll(filters)),
    enabled,
  })
}

export function usePa(id: string) {
  return useQuery({
    queryKey: ['pas', id],
    queryFn: () => paService.get(id),
    enabled: Boolean(id),
  })
}

export function usePaEvents(id: string) {
  return useQuery({
    queryKey: ['pas', id, 'events'],
    queryFn: () => paService.events(id),
    enabled: Boolean(id),
  })
}

export function useCreatePa() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreatePaBody) => paService.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pas'] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}

export function useUpdatePa() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdatePaBody }) =>
      paService.update(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['pas'] })
      queryClient.invalidateQueries({ queryKey: ['pas', id] })
    },
  })
}

export function useConfirmSettlement(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => paService.confirmSettlement(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pas'] })
      queryClient.invalidateQueries({ queryKey: ['pas', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

export function usePaAction(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: PaActionBody) => paService.action(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pas'] })
      queryClient.invalidateQueries({ queryKey: ['pas', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}
