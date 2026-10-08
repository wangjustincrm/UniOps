import { useQueries, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  poService,
  type PoFilters,
  type CreatePoBody,
  type UpdatePoBody,
  type ImportedDetailsBody,
  type PoActionBody,
  type PlaceOrderBody,
  type PoSignoffState,
  type ApiPo,
} from '@/services/po'
import { api } from '@/lib/api'
import { poAttachmentService } from '@/services/poAttachments'

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
// `enabled` defaults to true so every existing caller is unaffected; pass false
// where the list is only needed on one branch of a page (the PA editor does not
// need the whole PO list for an agreement-backed payment).
export function usePos(filters?: PoFilters, enabled = true) {
  const paged = filters?.page !== undefined || filters?.page_size !== undefined
  return useQuery({
    queryKey: ['pos', filters],
    queryFn: () => (paged ? poService.list(filters) : poService.listAll(filters)),
    enabled,
  })
}

export function usePo(id: string) {
  return useQuery({
    queryKey: ['pos', id],
    queryFn: () => poService.get(id),
    enabled: Boolean(id),
  })
}

// Several POs at once, keyed identically to usePo(id) so the caches are shared.
// Used where a document spans more than one purchase order — a payment
// application settling several of them, whose invoice/line comparisons need
// every PO's lines, not just the primary one's.
export function usePosByIds(ids: string[], enabled = true) {
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['pos', id],
      queryFn: () => poService.get(id),
      enabled,
    })),
  })
  const settled = ids.length === 0 || results.every((r) => r.data !== undefined)
  if (!settled) return { items: undefined as ApiPo[] | undefined, isLoading: true }
  return { items: results.map((r) => r.data).filter((p): p is ApiPo => p !== undefined), isLoading: false }
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

/** Buyer-detail edit for NC-imported POs (PATCH /po/{id}/imported-details).
 *  Separate from useUpdatePo: that one drives the general draft/returned edit
 *  form and can change the vendor, the currency and the whole line set. */
export function useUpdatePoImportedDetails(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ImportedDetailsBody) => poService.updateImportedDetails(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pos'] })
      queryClient.invalidateQueries({ queryKey: ['pos', id] })
    },
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

export function useUploadPoAttachment(poId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => poAttachmentService.upload(poId, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pos', poId, 'attachments'] }),
  })
}

export function useDeletePoAttachment(poId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (attId: string) => poAttachmentService.delete(poId, attId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pos', poId, 'attachments'] }),
  })
}

export function useRegeneratePoPdf(poId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<PoAttachmentMeta>(`/po/${poId}/attachments/regenerate-pdf`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pos', poId, 'attachments'] }),
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


// ── Sign-off (NC-imported POs) ────────────────────────────────────────────────
// Every mutation returns the full state, so the query cache is set from the
// response rather than refetched — the panel never blinks through a stale step.

export function usePoSignoff(id: string, enabled = true) {
  return useQuery<PoSignoffState>({
    queryKey: ['pos', id, 'signoff'],
    queryFn: () => poService.signoff(id),
    enabled: Boolean(id) && enabled,
  })
}

function useSignoffMutation<TArgs>(
  id: string,
  fn: (args: TArgs) => Promise<PoSignoffState>,
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (state) => {
      queryClient.setQueryData(['pos', id, 'signoff'], state)
      queryClient.invalidateQueries({ queryKey: ['pos', id] })
      queryClient.invalidateQueries({ queryKey: ['pos', id, 'attachments'] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

export function useSubmitPoSignoff(id: string) {
  return useSignoffMutation<string>(id, (justification) =>
    poService.submitSignoff(id, justification))
}

export function useSignPoSignoff(id: string) {
  return useSignoffMutation<string | undefined>(id, (comment) =>
    poService.signSignoff(id, comment))
}

export function useReturnPoSignoff(id: string) {
  return useSignoffMutation<string>(id, (comment) =>
    poService.returnSignoff(id, comment))
}

export function useAddPoSignoffNote(id: string) {
  return useSignoffMutation<string>(id, (comment) =>
    poService.addSignoffNote(id, comment))
}
