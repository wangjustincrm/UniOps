// Current user's effective permissions — served by identity, proxied through
// epms-api's GET /config/me/permissions (same endpoint mrp/finance/epms use).
//
// That endpoint returns the role-union matrix WITHOUT the system_admin
// short-circuit that the backend gates apply (packages/authz has_permission:
// "system_admin short-circuits"). A system_admin whose matrix row lacks a key
// would otherwise see a page hidden that sales-api would happily serve,
// so `can()` applies the same rule here.
import { useQuery } from '@tanstack/react-query'
import { epmsApi, hasSession } from '@/lib/api'
import { useModuleAuth } from '@/store/auth'

export interface MyPermissions {
  permissions: Record<string, boolean>
  roles: string[]
}

// The module's permission keys. The module is reachable with ANY of them;
// each nav item is gated by its own key (design §7, the Finance pattern).
export const MODULE_PERMS = ['sales.read', 'sales.order.operate', 'sales.file.retry', 'sales.transfer.manage', 'sales.inventory.adjust', 'sales.invoice.manage', 'sales.config'] as const

export function usePermissions() {
  const role = useModuleAuth((s) => s.user?.role ?? null)
  const query = useQuery<MyPermissions>({
    queryKey: ['my-permissions'],
    queryFn: () => epmsApi.get<MyPermissions>('/api/v1/config/me/permissions'),
    // Logged out there is nothing to ask: the layout is already on its way
    // to the Portal login (a request without a token is only a 403).
    enabled: hasSession(),
    staleTime: 60_000,
    retry: 1,
  })
  const isSystemAdmin = role === 'system_admin' || !!query.data?.roles.includes('system_admin')
  const can = (key: string): boolean => isSystemAdmin || query.data?.permissions?.[key] === true
  const canAny = (keys: readonly string[]): boolean => keys.some(can)
  return { ...query, isSystemAdmin, can, canAny }
}
