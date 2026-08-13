import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  invoiceService,
  type InvoiceFilters,
  type CreateInvoiceBody,
  type UpdateInvoiceBody,
  type MatchInvoiceBody,
  type SetReceiptsBody,
} from '@/services/invoices'
import type { ReceiptStatus } from '@/services/agreementReceipts'

export function useAssignMatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, userId }: { id: string; userId: string }) =>
      invoiceService.assignMatch(id, userId),
    onSuccess: (_d, { id }) => {
      qc.invalidateQueries({ queryKey: ['invoices'] })
      qc.invalidateQueries({ queryKey: ['invoices', id] })
    },
  })
}

export function useMatchCandidates(invoiceId: string, enabled = true) {
  return useQuery({
    queryKey: ['invoices', invoiceId, 'match-candidates'],
    queryFn: () => invoiceService.matchCandidates(invoiceId),
    enabled: Boolean(invoiceId) && enabled,
    staleTime: 30_000,
  })
}

// Invoice-scoped pickup receipt candidates (Task 10 review round 2, Finding B)
// — see invoiceService.agreementReceipts for why this hits a different route
// than useAgreementReceipts (hooks/useAgreementReceipts.ts), which stays on the
// agreement detail page's epms.agreement.read-gated endpoint.
export function useInvoiceAgreementReceipts(invoiceId: string, agreementId: string, status?: ReceiptStatus) {
  return useQuery({
    // Keyed UNDER ['agreement-receipts'], not under ['invoices'], and that is
    // the whole point: this is the THIRD view of the same rows, and it used to
    // sit in a namespace no receipt mutation touched. invalidateReceiptViews
    // (hooks/useAgreementReceipts.ts) refreshes ['agreement-receipts'] and
    // ['agreements', id, 'receipts'] — neither prefix-matches ['invoices', …],
    // so removing a receipt left THIS list serving it as claimable until a hard
    // reload. Ticking it then failed with a raw-UUID 422 from the server.
    //
    // Same trick useReceipt's ['agreement-receipts', 'detail', id] uses: put
    // the key under the prefix that is already invalidated and the refresh
    // becomes structural, instead of something every future mutation has to
    // remember. 'for-invoice' distinguishes it from useAllReceipts'
    // ['agreement-receipts', filters] — a string never collides with that
    // object.
    queryKey: ['agreement-receipts', 'for-invoice', invoiceId, agreementId, status],
    queryFn: () => invoiceService.agreementReceipts(invoiceId, agreementId, status),
    enabled: Boolean(invoiceId) && Boolean(agreementId),
  })
}

export function useDeclineMatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) =>
      invoiceService.declineMatch(id, note),
    onSuccess: (_d, { id }) => {
      qc.invalidateQueries({ queryKey: ['invoices'] })
      qc.invalidateQueries({ queryKey: ['invoices', id] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}

export function useReviewMatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, action, note }: { id: string; action: 'approve' | 'reject'; note?: string }) =>
      invoiceService.reviewMatch(id, action, note),
    onSuccess: (_d, { id }) => {
      qc.invalidateQueries({ queryKey: ['invoices'] })
      qc.invalidateQueries({ queryKey: ['invoices', id] })
    },
  })
}

// Without explicit page/page_size the caller wants the complete list, so we
// page through the API (server defaults to 20 rows and silently truncates).
export function useInvoices(filters?: InvoiceFilters, enabled = true) {
  const paged = filters?.page !== undefined || filters?.page_size !== undefined
  return useQuery({
    queryKey: ['invoices', filters],
    queryFn: () => (paged ? invoiceService.list(filters) : invoiceService.listAll(filters)),
    enabled,
    staleTime: 30_000,
  })
}

export function useInvoice(id: string) {
  return useQuery({
    queryKey: ['invoices', id],
    queryFn: () => invoiceService.get(id),
    enabled: Boolean(id),
    staleTime: 30_000,
  })
}

export function useCreateInvoice() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreateInvoiceBody) => invoiceService.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}

export function useUpdateInvoice() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & UpdateInvoiceBody) =>
      invoiceService.update(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.invalidateQueries({ queryKey: ['invoices', id] })
    },
  })
}

export function useMatchInvoice() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & MatchInvoiceBody) =>
      invoiceService.match(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.invalidateQueries({ queryKey: ['invoices', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

export function useDeleteInvoice() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => invoiceService.delete(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.removeQueries({ queryKey: ['invoices', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

// Task 7/8: full-override receipt mounting. onSuccess awaits both
// invalidations — TanStack Query v5's invalidateQueries only SCHEDULES a
// background refetch; without awaiting, the caller's own onSuccess (which
// closes an edit affordance / reads freshly-invalidated data) can run before
// the refetch lands, rendering one frame of stale receipt_ids.
export function useSetInvoiceReceipts() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & SetReceiptsBody) =>
      invoiceService.setReceipts(id, body),
    onSuccess: async (data, { id }) => {
      await Promise.all([
        // Fix-round 1 (Minor 2): ['invoices', id] and ['invoices', id, 'agreements']
        // don't prefix-match the list view's key (['invoices', filters]) — its
        // second element is a filters object, not this invoice's id, so without
        // this broader invalidation the Unmatched Queue / invoice list kept
        // showing the pre-mutation receipt_ids/legacy_settlement after a save.
        // A single ['invoices'] entry prefix-matches everything under it,
        // including the two more specific keys below, but they're left in
        // place for clarity about exactly what this mutation is known to affect.
        queryClient.invalidateQueries({ queryKey: ['invoices'] }),
        queryClient.invalidateQueries({ queryKey: ['invoices', id] }),
        queryClient.invalidateQueries({ queryKey: ['invoices', id, 'agreements'] }),
        // Whole-branch review (M1): this mutation flips agreement_receipts
        // rows between `open` and `reconciled` server-side
        // (crud/invoice.py set_receipts → agreement_receipt.claim/release),
        // so the two receipt-side caches are stale the moment it returns.
        // The reverse direction (useVoidReceiptAny / useApReviewReceiptAny in
        // hooks/useAgreementReceipts.ts) already invalidates both; only this
        // direction was one-way. Under the multi-tab shell, /receipts open
        // beside an invoice showed a just-claimed receipt as `open` with its
        // Void button live — clicking it 409s.
        //
        // agreement_id comes off the mutation RESULT, not the variables:
        // the request body carries only receipt ids, and the response is the
        // updated invoice (services/invoices.ts ApiInvoice.agreement_id). No
        // bare ['agreements'] invalidate — that prefix-matches every
        // agreement's data app-wide (see useCreateReceipt's comment).
        queryClient.invalidateQueries({ queryKey: ['agreement-receipts'] }),
        ...(data.agreement_id
          ? [queryClient.invalidateQueries({ queryKey: ['agreements', data.agreement_id, 'receipts'] })]
          : []),
      ])
    },
  })
}

// Task 7/8: explicit "no receipt evidence" declaration. Same await-before-
// onSuccess reasoning as useSetInvoiceReceipts above.
// Assigning the billing period changes the invoice AND consumes a schedule
// row, so both the invoice views and the agreement's schedule have to refresh —
// the agreement detail page renders that schedule, and a period left showing
// "pending" after it has been claimed is the same class of lie the receipt
// views were fixed for.
export function useAssignBillingPeriod() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, schedule_id }: { id: string; schedule_id: string }) =>
      invoiceService.assignBillingPeriod(id, schedule_id),
    onSuccess: async (data, { id }) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['invoices'] }),
        queryClient.invalidateQueries({ queryKey: ['invoices', id] }),
        queryClient.invalidateQueries({ queryKey: ['tasks'] }),
        ...(data.agreement_id
          ? [queryClient.invalidateQueries({ queryKey: ['agreements', data.agreement_id, 'schedule'] })]
          : []),
      ])
    },
  })
}

export function useSettleWithoutReceipt() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      invoiceService.settleWithoutReceipt(id, reason),
    onSuccess: async (data, { id }) => {
      await Promise.all([
        // Fix-round 1 (Minor 2): ['invoices', id] and ['invoices', id, 'agreements']
        // don't prefix-match the list view's key (['invoices', filters]) — its
        // second element is a filters object, not this invoice's id, so without
        // this broader invalidation the Unmatched Queue / invoice list kept
        // showing the pre-mutation receipt_ids/legacy_settlement after a save.
        // A single ['invoices'] entry prefix-matches everything under it,
        // including the two more specific keys below, but they're left in
        // place for clarity about exactly what this mutation is known to affect.
        queryClient.invalidateQueries({ queryKey: ['invoices'] }),
        queryClient.invalidateQueries({ queryKey: ['invoices', id] }),
        queryClient.invalidateQueries({ queryKey: ['invoices', id, 'agreements'] }),
        // Whole-branch review (M1): declaring "no receipt evidence" RELEASES
        // every receipt this invoice currently holds back to `open`
        // (crud/invoice.py settle_without_receipt →
        // _release_agreement_evidence), so the two receipt-side caches are
        // stale the moment it returns.
        // The reverse direction (useVoidReceiptAny / useApReviewReceiptAny in
        // hooks/useAgreementReceipts.ts) already invalidates both; only this
        // direction was one-way. Under the multi-tab shell, /receipts open
        // beside an invoice showed a just-claimed receipt as `open` with its
        // Void button live — clicking it 409s.
        //
        // agreement_id comes off the mutation RESULT, not the variables:
        // the request body carries only receipt ids, and the response is the
        // updated invoice (services/invoices.ts ApiInvoice.agreement_id). No
        // bare ['agreements'] invalidate — that prefix-matches every
        // agreement's data app-wide (see useCreateReceipt's comment).
        queryClient.invalidateQueries({ queryKey: ['agreement-receipts'] }),
        ...(data.agreement_id
          ? [queryClient.invalidateQueries({ queryKey: ['agreements', data.agreement_id, 'receipts'] })]
          : []),
      ])
    },
  })
}

export function useResolveException() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      id,
      resolution,
      note,
    }: {
      id: string
      resolution: 'accepted' | 'credit_note_requested'
      note?: string
    }) => invoiceService.resolveException(id, resolution, note),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.invalidateQueries({ queryKey: ['invoices', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}
