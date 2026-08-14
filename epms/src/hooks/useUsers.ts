import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { userService, type CreateUserBody, type UpdateUserBody, type UserFilters } from '@/services/users'

export function useUsers(filters?: Omit<UserFilters, 'page' | 'page_size'>) {
  return useQuery({
    queryKey: ['users', filters],
    queryFn: () => userService.listAll(filters),
  })
}

// GET /users is system_admin-only (require_roles("system_admin") in
// app/api/v1/users.py) — useUsers() above 403s for everyone else. GET
// /users/directory is open to any authenticated user and carries full_name,
// so this is the hook to use for "resolve a UUID to a display name" from a
// non-admin page. Pages through to the real total (server caps page_size at
// 100) rather than silently dropping anyone past the first page.
export function useUserDirectory(opts?: { search?: string; role?: string; department_id?: string; department_ids?: string[] }) {
  return useQuery({
    queryKey: ['users-directory', opts],
    queryFn: () => userService.directoryAll(opts),
  })
}

export function useCreateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateUserBody) => userService.create(body),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['users'] }) },
  })
}

export function useUpdateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateUserBody }) => userService.update(id, body),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['users'] }) },
  })
}

export function useDeleteUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => userService.delete(id),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['users'] }) },
  })
}
