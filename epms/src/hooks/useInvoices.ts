import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  invoiceService,
  type InvoiceFilters,
  type CreateInvoiceBody,
  type UpdateInvoiceBody,
  type MatchInvoiceBody,
} from '@/services/invoices'

// Without explicit page/page_size the caller wants the complete list, so we
// page through the API (server defaults to 20 rows and silently truncates).
export function useInvoices(filters?: InvoiceFilters, enabled = true) {
  const paged = filters?.page !== undefined || filters?.page_size !== undefined
  return useQuery({
    queryKey: ['invoices', filters],
    queryFn: () => (paged ? invoiceService.list(filters) : invoiceService.listAll(filters)),
    enabled,
    staleTime: 30_000,
  })
}

export function useInvoice(id: string) {
  return useQuery({
    queryKey: ['invoices', id],
    queryFn: () => invoiceService.get(id),
    enabled: Boolean(id),
    staleTime: 30_000,
  })
}

export function useCreateInvoice() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreateInvoiceBody) => invoiceService.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}

export function useUpdateInvoice() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & UpdateInvoiceBody) =>
      invoiceService.update(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.invalidateQueries({ queryKey: ['invoices', id] })
    },
  })
}

export function useMatchInvoice() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & MatchInvoiceBody) =>
      invoiceService.match(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.invalidateQueries({ queryKey: ['invoices', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

export function useDeleteInvoice() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => invoiceService.delete(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.removeQueries({ queryKey: ['invoices', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

export function useResolveException() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      id,
      resolution,
      note,
    }: {
      id: string
      resolution: 'accepted' | 'credit_note_requested'
      note?: string
    }) => invoiceService.resolveException(id, resolution, note),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.invalidateQueries({ queryKey: ['invoices', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}
