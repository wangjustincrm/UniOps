/**
 * The caller's effective permissions.
 *
 * Served by epms-api, which resolves the union of a person's primary role and
 * any additional roles — the same set the backend's own gate uses, so what the
 * interface offers and what the API allows cannot drift apart.
 */
import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

interface MePermissions {
  permissions: Record<string, boolean>
  roles: string[]
}

export function usePermissions() {
  const { data, isLoading } = useQuery({
    queryKey: ['me', 'permissions'],
    queryFn: () => epmsApi.get<MePermissions>('/api/v1/config/me/permissions'),
    staleTime: 5 * 60_000,
  })

  const can = (key: string): boolean => {
    if (!data) return false
    if (data.roles?.includes('system_admin')) return true
    return data.permissions?.[key] === true
  }

  return { can, roles: data?.roles ?? [], isLoading }
}
