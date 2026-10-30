import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { QuerySpec } from './querySpec'
import type { ResourceClient } from './ResourceClient'

/** List query plus create / update / deactivate mutations that refresh it. */
export function useCrudResource<TRead, TCreate, TUpdate>(
  client: ResourceClient<TRead, TCreate, TUpdate>,
  spec: QuerySpec,
  { enabled = true }: { enabled?: boolean } = {},
) {
  const qc = useQueryClient()
  const key = ['platform-crud', client.basePath] as const
  const list = useQuery({
    queryKey: [...key, spec],
    queryFn: () => client.list(spec),
    placeholderData: keepPreviousData,
    enabled,
  })
  const refresh = () => qc.invalidateQueries({ queryKey: key })
  const create = useMutation({ mutationFn: (data: TCreate) => client.create(data), onSuccess: refresh })
  const update = useMutation({
    mutationFn: (a: { id: number; data: Partial<TUpdate>; version?: number }) => client.update(a.id, a.data, a.version),
    onSuccess: refresh,
  })
  const deactivate = useMutation({ mutationFn: (id: number) => client.deactivate(id), onSuccess: refresh })
  return { list, create, update, deactivate, refresh }
}
