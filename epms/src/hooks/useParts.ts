import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  partService,
  type ApiPart,
  type PartFilters,
  type PartListResponse,
  type CreatePartBody,
  type UpdatePartBody,
} from '@/services/parts'

// Without explicit page/page_size the caller wants the complete list, so we
// page through the API (server defaults to 20 rows and silently truncates).
export function useParts(filters?: PartFilters) {
  const paged = filters?.page !== undefined || filters?.page_size !== undefined
  return useQuery<PartListResponse>({
    queryKey: ['parts', filters],
    queryFn: () => (paged ? partService.list(filters) : partService.listAll(filters)),
  })
}

export function usePartCategories() {
  return useQuery<string[]>({
    queryKey: ['parts', 'categories'],
    queryFn: () => partService.categories(),
  })
}

export function usePart(id: string) {
  return useQuery({
    queryKey: ['parts', id],
    queryFn: () => partService.get(id),
    enabled: Boolean(id),
  })
}

export function useCreatePart() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreatePartBody) => partService.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['parts'] })
    },
  })
}

export function useUpdatePart() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdatePartBody }) =>
      partService.update(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['parts'] })
      queryClient.invalidateQueries({ queryKey: ['parts', id] })
    },
  })
}

export function useDeletePart() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => partService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['parts'] })
    },
  })
}

export function useExportParts() {
  return useMutation({
    mutationFn: () => partService.exportCsv(),
  })
}

export function useImportParts() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (file: File) => partService.importCsv(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['parts'] })
    },
  })
}
