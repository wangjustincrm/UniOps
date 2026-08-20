// Current user's effective permissions — served by identity, proxied through
// epms-api's GET /config/me/permissions (a same-DB direct read keyed off the
// caller's JWT `sub`, module-agnostic — see epms-api/app/api/v1/config.py's
// docstring: "safe for any authenticated caller"). MRP has no permissions
// endpoint of its own yet, so it reuses this one exactly like epms/oa/vms
// front-ends already do (epms/src/hooks/useConfig.ts's useRolePermissions),
// via the epmsApi client (lib/api.ts) that useBranding.ts already exercises.
import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export interface MyPermissions {
  permissions: Record<string, boolean>
  roles: string[]
}

export function usePermissions() {
  return useQuery<MyPermissions>({
    queryKey: ['my-permissions'],
    queryFn: () => epmsApi.get<MyPermissions>('/api/v1/config/me/permissions'),
    staleTime: 60_000,
    retry: 1,
  })
}
