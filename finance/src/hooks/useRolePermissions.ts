import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

/** role_code → { permission_key: bool }. Mirrors epms-api /config/role-permissions. */
export type RolePermissionMatrix = Record<string, Record<string, boolean>>

/**
 * Fetches the Access Control Matrix from epms-api so the Portal sidebar can gate
 * Finance items by the same toggles edited in EPMS → Admin Panel → Access Control
 * Matrix. Falls back to an empty matrix on error (items then hide for non-admins).
 */
export function useRolePermissions() {
  return useQuery<RolePermissionMatrix>({
    queryKey: ['portal-role-permissions'],
    queryFn: () => epmsApi.get<RolePermissionMatrix>('/config/role-permissions'),
    staleTime: 60_000,
    retry: 1,
  })
}
