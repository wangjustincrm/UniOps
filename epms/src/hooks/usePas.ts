import { useQueries, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  paService,
  type PaFilters,
  type CreatePaBody,
  type UpdatePaBody,
  type PaActionBody,
  type ApiPa,
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

// Multi-PO payment: the active PAs on every PO being paid. Same key shape as
// usePas({ po_id }) so the caches are shared. De-duplicated by id — a PA that
// already covers two of the selected POs comes back from both queries, and
// counting it twice would double its invoices in the "already claimed" lock.
export function usePasForPos(poIds: string[], enabled = true) {
  const results = useQueries({
    queries: poIds.map((poId) => ({
      queryKey: ['pas', { po_id: poId }],
      queryFn: () => paService.listAll({ po_id: poId }),
      enabled,
    })),
  })
  const settled = poIds.length === 0 || results.every((r) => r.data !== undefined)
  if (!settled) return { items: undefined as ApiPa[] | undefined, isLoading: true }
  const seen = new Set<string>()
  const items: ApiPa[] = []
  for (const r of results) {
    for (const pa of r.data?.items ?? []) {
      if (seen.has(pa.id)) continue
      seen.add(pa.id)
      items.push(pa)
    }
  }
  return { items, isLoading: false }
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

export function usePaWorkflowSteps(id: string) {
  return useQuery({
    queryKey: ['pa', id, 'workflow-steps'],
    queryFn: () => paService.workflowSteps(id),
    enabled: !!id,
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
    // Surface the server error (e.g. a 422 validation failure) rather than
    // swallowing it — the create pages catch the throw silently.
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to create payment application'),
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
    // Surface the server error (e.g. a 422 validation failure) rather than
    // swallowing it — the edit page catches the throw silently.
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to update payment application'),
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
