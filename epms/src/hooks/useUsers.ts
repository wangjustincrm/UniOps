import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { userService, type CreateUserBody, type UpdateUserBody, type UserFilters } from '@/services/users'

export function useUsers(filters?: Omit<UserFilters, 'page' | 'page_size'>) {
  return useQuery({
    queryKey: ['users', filters],
    queryFn: () => userService.listAll(filters),
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
