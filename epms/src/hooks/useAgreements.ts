import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  agreementService,
  type AgreementFilters,
  type CreateAgreementBody,
  type UpdateAgreementBody,
  type AgreementActionBody,
} from '@/services/agreement'

// Without explicit page/page_size the caller wants the complete list, so we
// page through the API (server defaults to 20 rows and silently truncates).
export function useAgreements(filters?: AgreementFilters) {
  const paged = filters?.page !== undefined || filters?.page_size !== undefined
  return useQuery({
    queryKey: ['agreements', filters],
    queryFn: () => (paged ? agreementService.list(filters) : agreementService.listAll(filters)),
  })
}

export function useAgreement(id: string) {
  return useQuery({
    queryKey: ['agreements', id],
    queryFn: () => agreementService.get(id),
    enabled: Boolean(id),
  })
}

export function useCreateAgreement() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreateAgreementBody) => agreementService.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agreements'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to create agreement'),
  })
}

export function useUpdateAgreement() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateAgreementBody }) =>
      agreementService.update(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['agreements'] })
      queryClient.invalidateQueries({ queryKey: ['agreements', id] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to update agreement'),
  })
}

export function useAgreementAction(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: AgreementActionBody) => agreementService.action(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agreements'] })
      queryClient.invalidateQueries({ queryKey: ['agreements', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Agreement action failed'),
  })
}
