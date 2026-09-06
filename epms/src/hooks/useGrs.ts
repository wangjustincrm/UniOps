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
export function useGrsForPos(poIds: string[], enabled = true) {
  const results = useQueries({
    queries: poIds.map((poId) => ({
      queryKey: ['grs', { po_id: poId }],
      queryFn: () => grService.listAll({ po_id: poId }),
      enabled,
      staleTime: 30_000,
    })),
  })
  const settled = poIds.length === 0 || results.every((r) => r.data !== undefined)
  if (!settled) return { items: undefined as ApiGr[] | undefined, isLoading: true }
  return { items: results.flatMap((r) => r.data?.items ?? []), isLoading: false }
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
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}
