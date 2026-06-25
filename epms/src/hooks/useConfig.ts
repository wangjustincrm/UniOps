import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  configService,
  type UpdateConfigBody,
  type CreateTempAssignmentBody,
  type UpdateRolePermissionsBody,
  type CreateCustomRoleBody,
  type UpdateCustomRoleBody,
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

export function useRolePermissions() {
  return useQuery({
    queryKey: ['role-permissions'],
    queryFn: () => configService.getRolePermissions(),
    staleTime: 30_000,
  })
}

export function useUpdateRolePermissions() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: UpdateRolePermissionsBody) =>
      configService.updateRolePermissions(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['role-permissions'] })
    },
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

export function useCreateRole() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateCustomRoleBody) => configService.createRole(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] })
      queryClient.invalidateQueries({ queryKey: ['role-permissions'] })
    },
  })
}

export function useUpdateRole() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ code, body }: { code: string; body: UpdateCustomRoleBody }) =>
      configService.updateRole(code, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] })
    },
  })
}

export function useDeleteRole() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (code: string) => configService.deleteRole(code),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] })
      queryClient.invalidateQueries({ queryKey: ['role-permissions'] })
    },
  })
}
