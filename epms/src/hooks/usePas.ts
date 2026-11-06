import { useQueries, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  paService,
  type PaFilters,
  type CreatePaBody,
  type CreateDirectPaBody,
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
//
// `combine` is module-level so the result keeps its identity until the data
// changes — see combineGrs in useGrs.ts (ISS-0097).
function combinePas(results: { data?: { items: ApiPa[] } }[]) {
  if (!results.every((r) => r.data !== undefined)) return { items: undefined as ApiPa[] | undefined, isLoading: true }
  const seen = new Set<string>()
  const items: ApiPa[] = []
  for (const r of results) {
    for (const pa of r.data?.items ?? []) {
      if (seen.has(pa.id)) continue
      seen.add(pa.id)
      items.push(pa)
    }
  }
  return { items: items as ApiPa[] | undefined, isLoading: false }
}

export function usePasForPos(poIds: string[], enabled = true) {
  return useQueries({
    queries: poIds.map((poId) => ({
      queryKey: ['pas', { po_id: poId }],
      queryFn: () => paService.listAll({ po_id: poId }),
      enabled,
    })),
    combine: combinePas,
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
    // No onError alert: New PA shows the server's refusal (e.g. a 422) in its
    // red error box from the mutation's error state (ISS-0188).
  })
}

export function useDirectPaCandidates() {
  return useQuery({
    queryKey: ['pas', 'direct-candidates'],
    queryFn: () => paService.directCandidates(),
  })
}

// No onError alert: the Direct PA form shows the server's message inline.
export function useCreateDirectPa() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateDirectPaBody) => paService.createDirect(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pas'] })
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
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
    // No onError alert: the edit pages show the server's refusal inline from
    // the mutation's error state (ISS-0188).
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
      // The final approval stores the PA PDF as an attachment (PA-006); the
      // Attachments tab showed it only after F5 (ISS-0104).
      queryClient.invalidateQueries({ queryKey: ['pa-attachments', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}
