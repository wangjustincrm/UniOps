import { useQueries, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  freightService,
  freightAttachmentService,
  type ShipmentFilters,
  type ShipmentCreateBody,
  type ShipmentUpdateBody,
  type InvoiceFilters,
  type FreightInvoiceCreateBody,
  type FreightLineUpdateBody,
  type FreightPaCreateBody,
  type FreightInvoice,
} from '@/services/freight'

// Query keys — everything Freight lives under ['freight', …] so one
// invalidation can refresh the whole page family when needed.
const K = {
  me: ['freight', 'me'] as const,
  carriers: ['freight', 'carriers'] as const,
  shipments: ['freight', 'shipments'] as const,
  invoices: ['freight', 'invoices'] as const,
  invoice: (id: string) => ['freight', 'invoice', id] as const,
  attachments: (id: string) => ['freight', 'invoice', id, 'attachments'] as const,
}

export function useFreightAccess() {
  return useQuery({ queryKey: K.me, queryFn: () => freightService.me(), staleTime: 60_000 })
}

export function useFreightCarriers(enabled = true) {
  return useQuery({ queryKey: K.carriers, queryFn: () => freightService.carriers(), enabled })
}

export function useFreightShipments(filters: ShipmentFilters, enabled = true) {
  return useQuery({
    queryKey: [...K.shipments, filters],
    queryFn: () => freightService.shipments(filters),
    enabled,
  })
}

export function useFreightInvoices(filters: InvoiceFilters, enabled = true) {
  return useQuery({
    queryKey: [...K.invoices, filters],
    queryFn: () => freightService.invoices(filters),
    enabled,
  })
}

export function useFreightInvoice(id: string, enabled = true) {
  return useQuery({
    queryKey: K.invoice(id),
    queryFn: () => freightService.invoice(id),
    enabled: !!id && enabled,
  })
}

/** Several invoice details at once (Create PA preview sums their cost split). */
export function useFreightInvoiceDetails(ids: string[]) {
  const results = useQueries({
    queries: ids.map((id) => ({ queryKey: K.invoice(id), queryFn: () => freightService.invoice(id) })),
  })
  const items = results.map((r) => r.data).filter((d): d is FreightInvoice => !!d)
  return { items, isLoading: results.some((r) => r.isLoading), isError: results.some((r) => r.isError) }
}

export function useFreightInvoiceAttachments(id: string) {
  return useQuery({
    queryKey: K.attachments(id),
    queryFn: () => freightAttachmentService.list(id),
    enabled: !!id,
  })
}

// ── Shipment mutations ───────────────────────────────────────────────────────

export function useCreateShipment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ShipmentCreateBody) => freightService.createShipment(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: K.shipments }),
  })
}

export function useUpdateShipment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ShipmentUpdateBody }) =>
      freightService.updateShipment(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: K.shipments }),
  })
}

export function useVoidShipment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => freightService.voidShipment(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: K.shipments }),
  })
}

// ── Invoice mutations ────────────────────────────────────────────────────────

/** Matching moves shipments between pending/billed and invoices between
 *  unmatched/matched — every invoice-level change refreshes both lists. */
function useInvoiceResult() {
  const qc = useQueryClient()
  return (inv: FreightInvoice) => {
    qc.setQueryData(K.invoice(inv.id), inv)
    qc.invalidateQueries({ queryKey: K.invoices })
    qc.invalidateQueries({ queryKey: K.shipments })
  }
}

export function useCreateFreightInvoice() {
  const apply = useInvoiceResult()
  return useMutation({
    mutationFn: (body: FreightInvoiceCreateBody) => freightService.createInvoice(body),
    onSuccess: apply,
  })
}

export function useAutoMatch(id: string) {
  const apply = useInvoiceResult()
  return useMutation({ mutationFn: () => freightService.autoMatch(id), onSuccess: apply })
}

export function useUpdateFreightLine(id: string) {
  const apply = useInvoiceResult()
  return useMutation({
    mutationFn: ({ lineId, body }: { lineId: string; body: FreightLineUpdateBody }) =>
      freightService.updateLine(id, lineId, body),
    onSuccess: apply,
  })
}

export function useFinalizeFreightInvoice(id: string) {
  const apply = useInvoiceResult()
  return useMutation({ mutationFn: () => freightService.finalize(id), onSuccess: apply })
}

export function useReopenFreightInvoice(id: string) {
  const apply = useInvoiceResult()
  return useMutation({ mutationFn: () => freightService.reopen(id), onSuccess: apply })
}

export function useDeleteFreightInvoice(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => freightService.deleteInvoice(id),
    onSuccess: () => {
      qc.removeQueries({ queryKey: K.invoice(id) })
      qc.invalidateQueries({ queryKey: K.invoices })
      qc.invalidateQueries({ queryKey: K.shipments })
    },
  })
}

export function useCreateFreightPa() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: FreightPaCreateBody) => freightService.createPa(body),
    onSuccess: (_res, body) => {
      qc.invalidateQueries({ queryKey: K.invoices })
      for (const id of body.invoice_ids) qc.invalidateQueries({ queryKey: K.invoice(id) })
      qc.invalidateQueries({ queryKey: ['pas'] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}

export function useUploadFreightAttachment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ invoiceId, file }: { invoiceId: string; file: File }) =>
      freightAttachmentService.upload(invoiceId, file),
    onSuccess: (_r, { invoiceId }) => qc.invalidateQueries({ queryKey: K.attachments(invoiceId) }),
  })
}
