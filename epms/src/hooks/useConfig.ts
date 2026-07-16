import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  configService,
  type UpdateConfigBody,
} from '@/services/config'

export function useConfig() {
  return useQuery({
    queryKey: ['config'],
    queryFn: () => configService.get(),
    staleTime: 60_000,
  })
}

export function useUpdateConfig() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: UpdateConfigBody) => configService.update(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config'] })
    },
  })
}

// ── Role Permissions ──────────────────────────────────────────────────────────

/** Current user's effective permissions (primary ∪ additional roles),
 *  served by identity via the epms proxy. */
export function useRolePermissions() {
  return useQuery({
    queryKey: ['my-permissions'],
    queryFn: () => configService.getMyPermissions(),
    staleTime: 60_000,
    retry: 1,
  })
}

/** Every user's ADDITIONAL roles only (identity user_roles assignments) —
 *  no primary role mixed in. Used to resolve job-function assignments like
 *  finance_bp, where holding the function as your primary role must NOT
 *  count (see BudgetDashboard.tsx). */
export function useUserRoles() {
  return useQuery({
    queryKey: ['config-user-roles'],
    queryFn: () => configService.getUserRoles(),
    staleTime: 60_000,
  })
}

export function useLockedPermissions() {
  return useQuery({
    queryKey: ['locked-permissions'],
    queryFn: () => configService.getLockedPermissions(),
    staleTime: Infinity,
  })
}

export function usePermissionKeys() {
  return useQuery({
    queryKey: ['permission-keys'],
    queryFn: () => configService.getPermissionKeys(),
    staleTime: Infinity,
  })
}

// ── Custom Roles ──────────────────────────────────────────────────────────────

export function useRoles() {
  return useQuery({
    queryKey: ['roles'],
    queryFn: () => configService.listRoles(),
    staleTime: 30_000,
  })
}
