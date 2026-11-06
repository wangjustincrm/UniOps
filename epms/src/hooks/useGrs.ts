import { useQueries, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  grService,
  type GrFilters,
  type CreateGrBody,
  type GrActionBody,
  type ApiGr,
} from '@/services/gr'

// Without explicit page/page_size the caller wants the complete list, so we
// page through the API (server defaults to 20 rows and silently truncates).
export function useGrs(filters?: GrFilters, enabled = true) {
  const paged = filters?.page !== undefined || filters?.page_size !== undefined
  return useQuery({
    queryKey: ['grs', filters],
    queryFn: () => (paged ? grService.list(filters) : grService.listAll(filters)),
    enabled,
    staleTime: 30_000,
  })
}

// Multi-PO payment: one query per PO, keyed identically to useGrs({ po_id })
// so the caches are shared and existing ['grs'] invalidations still apply.
// GR ids are unique per PO, so no de-duplication is needed here.
//
// `combine` is a module-level function on purpose: TanStack then hands back the
// SAME result object until a query's data really changes. Built inline, `items`
// was a new array on every render, and New PA — whose effects depend on it —
// re-rendered itself forever (ISS-0097).
function combineGrs(results: { data?: { items: ApiGr[] } }[]) {
  if (!results.every((r) => r.data !== undefined)) return { items: undefined as ApiGr[] | undefined, isLoading: true }
  return { items: results.flatMap((r) => r.data?.items ?? []) as ApiGr[] | undefined, isLoading: false }
}

export function useGrsForPos(poIds: string[], enabled = true) {
  return useQueries({
    queries: poIds.map((poId) => ({
      queryKey: ['grs', { po_id: poId }],
      queryFn: () => grService.listAll({ po_id: poId }),
      enabled,
      staleTime: 30_000,
    })),
    combine: combineGrs,
  })
}

// Keyed under 'grs' so every GR create/action invalidation refreshes it.
export function usePoOpenQuantities(poId: string) {
  return useQuery({
    queryKey: ['grs', 'open-quantities', poId],
    queryFn: () => grService.openQuantities(poId),
    enabled: Boolean(poId),
  })
}

export function useGr(id: string) {
  return useQuery({
    queryKey: ['grs', id],
    queryFn: () => grService.get(id),
    enabled: Boolean(id),
  })
}

export function useCreateGr() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreateGrBody) => grService.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['grs'] })
      queryClient.invalidateQueries({ queryKey: ['pos'] })
    },
  })
}

export function useGrAction(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: GrActionBody) => grService.action(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['grs'] })
      queryClient.invalidateQueries({ queryKey: ['grs', id] })
      // Confirming a GR (service completion, receipt) stores its PDF as an
      // attachment (GR-S-007); the Attachments tab showed it only after F5
      // (ISS-0104).
      queryClient.invalidateQueries({ queryKey: ['gr-attachments', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}
