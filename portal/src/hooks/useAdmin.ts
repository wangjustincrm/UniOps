import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { adminApi } from '@/services/adminApi'

export const useAdminEntities = () =>
  useQuery({ queryKey: ['admin', 'entities'], queryFn: adminApi.entities })

export const useAdminList = (system: string, entity: string, page: number, search: string) =>
  useQuery({
    queryKey: ['admin', system, entity, page, search],
    queryFn: () => adminApi.list(system, entity, { page, page_size: 20, search: search || undefined }),
    enabled: !!entity,
  })

export const useAdminEdit = (system: string, entity: string) => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Record<string, unknown> }) =>
      adminApi.edit(system, entity, id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', system, entity] }),
  })
}

export const useAdminDelete = (system: string, entity: string) => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => adminApi.remove(system, entity, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', system, entity] }),
  })
}

export const useAdminBulkDelete = (system: string, entity: string) => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (ids: string[]) => adminApi.bulkDelete(system, entity, ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', system, entity] }),
  })
}
