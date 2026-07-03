import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  prService,
  type PrFilters,
  type CreatePrBody,
  type UpdatePrBody,
  type PrActionBody,
} from '@/services/pr'

// Without explicit page/page_size the caller wants the complete list, so we
// page through the API (server defaults to 20 rows and silently truncates).
export function usePrs(filters?: PrFilters) {
  const paged = filters?.page !== undefined || filters?.page_size !== undefined
  return useQuery({
    queryKey: ['prs', filters],
    queryFn: () => (paged ? prService.list(filters) : prService.listAll(filters)),
  })
}

export function usePr(id: string) {
  return useQuery({
    queryKey: ['prs', id],
    queryFn: () => prService.get(id),
    enabled: Boolean(id),
  })
}

export function usePrEvents(id: string) {
  return useQuery({
    queryKey: ['prs', id, 'events'],
    queryFn: () => prService.events(id),
    enabled: Boolean(id),
  })
}

export function usePrWorkflowSteps(id: string) {
  return useQuery({
    queryKey: ['pr', id, 'workflow-steps'],
    queryFn: () => prService.workflowSteps(id),
    enabled: !!id,
  })
}

export function useCreatePr() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreatePrBody) => prService.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prs'] })
    },
  })
}

export function useUpdatePr() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdatePrBody }) =>
      prService.update(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['prs'] })
      queryClient.invalidateQueries({ queryKey: ['prs', id] })
    },
  })
}

export function usePrAction(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: PrActionBody) => prService.action(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prs'] })
      queryClient.invalidateQueries({ queryKey: ['prs', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}
