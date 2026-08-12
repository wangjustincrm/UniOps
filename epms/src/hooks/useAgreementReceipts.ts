import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  agreementReceiptService,
  type CreateReceiptBody,
  type ReceiptApReviewAction,
  type ReceiptListAllFilters,
  type ReceiptStatus,
} from '@/services/agreementReceipts'

export function useAgreementReceipts(agreementId: string, status?: ReceiptStatus) {
  return useQuery({
    queryKey: ['agreements', agreementId, 'receipts', status],
    queryFn: () => agreementReceiptService.list(agreementId, status ? { status } : undefined),
    enabled: Boolean(agreementId),
  })
}

// Cross-agreement listing (Task 9) — backs ReceiptListPage, its own menu
// entry. A DELIBERATELY SEPARATE top-level query-key namespace, NOT
// ['agreements', ...] — see receiptAttachmentsQueryKey's comment in
// components/agreements/ReceiptTable.tsx for why sharing that branch would
// make this page's fetches collateral damage of every single-agreement
// receipt mutation's invalidateQueries prefix-match.
export function useAllReceipts(filters?: ReceiptListAllFilters) {
  return useQuery({
    queryKey: ['agreement-receipts', filters],
    queryFn: () => agreementReceiptService.listAll(filters),
  })
}

export function useCreateReceipt(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreateReceiptBody) => agreementReceiptService.create(agreementId, body),
    // await is load-bearing — see useConfirmPeriod/useAgreementAction in
    // hooks/useAgreements.ts for the same rationale: invalidateQueries only
    // *schedules* a background refetch, so without awaiting it isPending
    // flips false and the entry form re-arms while the receipt list still shows
    // stale (pre-create) data.
    //
    // Scoped to THIS agreement's receipt list only — no ['agreements'] (bare)
    // invalidate. That broad key used to prefix-match every agreement's data
    // app-wide (every OTHER open agreement's receipts/attachments too, plus the
    // agreement list/header queries), and, within this agreement, every
    // per-row attachment query as collateral damage on every single mutation.
    // It was also redundant with the narrow invalidate right below it.
    // consumed_amount/NTE on the agreement header is derived from matched
    // INVOICES (crud/invoice.py, e.g. _recompute_consumed), never from
    // pickup receipts directly — create/void/ap-review touch nothing on the
    // agreement row itself, so there is nothing on ['agreements'] for these
    // three mutations to invalidate in the first place.
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'receipts'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to create receipt'),
  })
}

export function useVoidReceipt(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (receiptId: string) => agreementReceiptService.void(agreementId, receiptId),
    // See useCreateReceipt above for why this doesn't also invalidate ['agreements'].
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'receipts'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to void receipt'),
  })
}

export function useApReviewReceipt(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ receiptId, action }: { receiptId: string; action: ReceiptApReviewAction }) =>
      agreementReceiptService.apReview(agreementId, receiptId, action),
    // See useCreateReceipt above for why this doesn't also invalidate ['agreements'].
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'receipts'] })
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to record AP review'),
  })
}

// ─── Cross-agreement variants (Task 10 fix round 1) ────────────────────────
//
// ReceiptListPage (GET /agreement-receipts) spans every agreement at once, so
// unlike useVoidReceipt/useApReviewReceipt above — which close over a single
// agreementId at hook-construction time because they were built for the
// per-agreement ReceiptTable — these take agreementId per call, read off each
// row's own receipt.agreement_id. Introduced because Void/AP-review lost their
// only UI surface when AgreementDetailPage's ReceiptTable went readOnly: a
// receipt stuck at pending_ap_review had no route out anywhere in the app.
//
// Invalidates BOTH the cross-agreement list key (so this page reflects the
// change) AND the single-agreement key (so that agreement's detail page,
// if open in another tab, doesn't show stale status next time it refetches).

export function useVoidReceiptAny() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ agreementId, receiptId }: { agreementId: string; receiptId: string }) =>
      agreementReceiptService.void(agreementId, receiptId),
    // await is load-bearing, same rationale as every other mutation in this
    // file — invalidateQueries only *schedules* a refetch; without awaiting,
    // isPending flips false and the row's buttons re-arm while the list still
    // shows the pre-void status.
    onSuccess: async (_data, { agreementId }) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['agreement-receipts'] }),
        queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'receipts'] }),
      ])
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to void receipt'),
  })
}

export function useApReviewReceiptAny() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ agreementId, receiptId, action }: { agreementId: string; receiptId: string; action: ReceiptApReviewAction }) =>
      agreementReceiptService.apReview(agreementId, receiptId, action),
    onSuccess: async (_data, { agreementId }) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['agreement-receipts'] }),
        queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'receipts'] }),
      ])
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to record AP review'),
  })
}
