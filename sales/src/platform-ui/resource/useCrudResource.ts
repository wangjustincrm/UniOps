import { useMutation, useQueryClient } from '@tanstack/react-query'
import { queryKeys } from './queryKeys'
import type { ResourceClient } from './ResourceClient'

/** Create / update / deactivate for one resource; each refreshes its lists
 *  (the list itself is ListPage's query, keyed by queryKeys.list). */
export function useCrudResource<TRead, TCreate, TUpdate>(client: ResourceClient<TRead, TCreate, TUpdate>) {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: queryKeys.list(client) })
  const create = useMutation({ mutationFn: (data: TCreate) => client.create(data), onSuccess: refresh })
  const update = useMutation({
    mutationFn: (a: { id: number; data: Partial<TUpdate>; version?: number }) => client.update(a.id, a.data, a.version),
    onSuccess: refresh,
  })
  const deactivate = useMutation({ mutationFn: (id: number) => client.deactivate(id), onSuccess: refresh })
  return { create, update, deactivate, refresh }
}
