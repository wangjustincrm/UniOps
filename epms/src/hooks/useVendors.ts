import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  vendorService,
  type VendorFilters,
  type CreateVendorBody,
  type UpdateVendorBody,
} from '@/services/vendors'
export type { VendorImportResult } from '@/services/vendors'

// Without explicit page/page_size the caller wants the complete list, so we
// page through the API (server defaults to 20 rows and silently truncates).
export function useVendors(filters?: VendorFilters) {
  const paged = filters?.page !== undefined || filters?.page_size !== undefined
  return useQuery({
    queryKey: ['vendors', filters],
    queryFn: () => (paged ? vendorService.list(filters) : vendorService.listAll(filters)),
    staleTime: 30_000,
  })
}

export function useVendor(id: string) {
  return useQuery({
    queryKey: ['vendors', id],
    queryFn: () => vendorService.get(id),
    enabled: Boolean(id),
  })
}

export function useCreateVendor() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreateVendorBody) => vendorService.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vendors'] })
    },
  })
}

export function useUpdateVendor() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateVendorBody }) =>
      vendorService.update(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['vendors'] })
      queryClient.invalidateQueries({ queryKey: ['vendors', id] })
    },
  })
}

export function useImportVendors() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (file: File) => vendorService.importCsv(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vendors'] })
    },
  })
}
