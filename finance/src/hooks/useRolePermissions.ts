import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export type MyPermissions = { permissions: Record<string, boolean>; roles: string[] }

/** Current user's effective permissions (primary ∪ additional roles),
 *  served by identity via the epms proxy. */
export function useRolePermissions() {
  return useQuery<MyPermissions>({
    queryKey: ['my-permissions'],
    queryFn: () => epmsApi.get<MyPermissions>('/config/me/permissions'),
    staleTime: 60_000,
    retry: 1,
  })
}
