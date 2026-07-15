import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  configService,
  type UpdateConfigBody,
  type CreateTempAssignmentBody,
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

export function useCreateTempAssignment() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreateTempAssignmentBody) =>
      configService.createTempAssignment(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config'] })
    },
  })
}

export function useDeleteTempAssignment() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => configService.deleteTempAssignment(id),
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
