import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  agreementService,
  type AgreementFilters,
  type CreateAgreementBody,
  type UpdateAgreementBody,
  type AgreementActionBody,
} from '@/services/agreement'
import { invoiceService } from '@/services/invoices'

// Without explicit page/page_size the caller wants the complete list, so we
// page through the API (server defaults to 20 rows and silently truncates).
export function useAgreements(filters?: AgreementFilters) {
  const paged = filters?.page !== undefined || filters?.page_size !== undefined
  return useQuery({
    queryKey: ['agreements', filters],
    queryFn: () => (paged ? agreementService.list(filters) : agreementService.listAll(filters)),
  })
}

// Agreement candidates for a given invoice (same vendor, admission window) —
// backs the Agreements tab in MatchPanel. Task 9: no client-side filtering on
// top of what the server returns, per the brief — the server's admission
// window rule is authoritative.
export function useAgreementCandidates(invoiceId: string, enabled = true) {
  return useQuery({
    queryKey: ['invoices', invoiceId, 'agreement-candidates'],
    queryFn: () => invoiceService.agreementCandidates(invoiceId),
    enabled: Boolean(invoiceId) && enabled,
    staleTime: 30_000,
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

export function useAgreementSchedule(agreementId: string) {
  return useQuery({
    queryKey: ['agreements', agreementId, 'schedule'],
    queryFn: () => agreementService.schedule(agreementId),
    enabled: Boolean(agreementId),
  })
}

export function useConfirmPeriod(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (rowId: string) => agreementService.confirmPeriod(agreementId, rowId),
    // await is load-bearing, same rationale as useAgreementAction above: without
    // it isPending flips false before the schedule/task refetch lands, the
    // Confirm button re-arms on stale data, and a second click 409s against an
    // already-confirmed row. Both keys gate visibility here — the schedule
    // table derives its own status column, and the task list backs whether
    // the caller still holds an open confirm_period task for this row.
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['agreements'] }),
        queryClient.invalidateQueries({ queryKey: ['tasks'] }),
      ])
    },
    onError: (err: unknown) =>
      alert(err instanceof Error ? err.message : 'Failed to confirm the period'),
  })
}

export function useAgreementAction(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: AgreementActionBody) => agreementService.action(id, body),
    // The await is load-bearing, not tidiness. invalidateQueries only *schedules*
    // a background refetch; without awaiting it, the mutation settles immediately,
    // isPending flips false, and AgreementDetailPage re-enables "Submit for
    // Approval" while the header still renders the pre-action status. A second
    // click in that window posts submit against an already-submitted agreement
    // and the backend correctly answers 409 ("Cannot submit AGR in status
    // 'submitted'") — which reads to the user as "submit is broken" even though
    // it worked. Awaiting keeps isPending true until the refetch settles, so the
    // button is only re-armed once the status on screen is the real one.
    // The ['agreements'] key prefix-matches both the list and ['agreements', id],
    // so one await covers the detail query too. It never rejects: refetch errors
    // land in query state, not the promise.
    //
    // ['tasks'] is awaited for the same reason, one step later: the detail page
    // derives canApprove from the open-task list, and approving at step 0 leaves
    // the status *still* approvable ('in_review'). Refresh the agreement without
    // the tasks and the Approve button re-renders off a completed task.
    // ['dashboard'] gates no control here, so it stays fire-and-forget.
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['agreements'] }),
        queryClient.invalidateQueries({ queryKey: ['tasks'] }),
      ])
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Agreement action failed'),
  })
}
