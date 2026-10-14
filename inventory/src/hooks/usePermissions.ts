// Current user's effective permissions — served by identity, proxied through
// epms-api's GET /config/me/permissions (same endpoint mrp/finance/epms use).
//
// That endpoint returns the role-union matrix WITHOUT the system_admin
// short-circuit that the backend gates apply (packages/authz has_permission:
// "system_admin short-circuits"). A system_admin whose matrix row lacks a key
// would otherwise see a page hidden that inventory-api would happily serve,
// so `can()` applies the same rule here.
import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'
import { useInventoryAuth } from '@/store/auth'

export interface MyPermissions {
  permissions: Record<string, boolean>
  roles: string[]
}

export const PERM_VIEW = 'inventory.view'
export const PERM_COST_VIEW = 'inventory.cost.view'
export const PERM_ADMIN = 'inventory.admin'

export function usePermissions() {
  const role = useInventoryAuth((s) => s.user?.role ?? null)
  const query = useQuery<MyPermissions>({
    queryKey: ['my-permissions'],
    queryFn: () => epmsApi.get<MyPermissions>('/api/v1/config/me/permissions'),
    staleTime: 60_000,
    retry: 1,
  })
  const isSystemAdmin = role === 'system_admin' || !!query.data?.roles.includes('system_admin')
  const can = (key: string): boolean => isSystemAdmin || query.data?.permissions?.[key] === true
  return { ...query, isSystemAdmin, can }
}
