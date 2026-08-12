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

// ─── Invalidation, in exactly one place ───────────────────────────────────
//
// A receipt is visible through TWO query-key namespaces that share no prefix:
// ['agreements', id, 'receipts'] (the agreement detail page's read-only table)
// and ['agreement-receipts'] (ReceiptListPage, its own menu entry). Every
// mutation below has to refresh both, and every mutation below used to decide
// that for itself — which is how useCreateReceipt shipped invalidating only
// the first: a receipt saved from the create page was in the database and on
// the agreement page, but ReceiptListPage kept serving its cached list until a
// hard reload. The whole-branch review caught the same asymmetry on the
// invoice-side hooks and nobody looked back at the create path.
//
// Routing every mutation through this helper makes "refresh only one side"
// syntactically impossible, the same way crud/invoice.py's _set_agreement_link
// makes writing one of the three agreement columns without the others
// impossible.
//
// Deliberately NOT invalidating bare ['agreements']: that prefix-matches every
// other agreement's data app-wide plus every per-row attachment query, and
// there is nothing on the agreement row itself for these mutations to refresh
// — consumed_amount/NTE is derived from matched INVOICES (crud/invoice.py's
// _recompute_consumed), never from receipts.
//
// await is load-bearing throughout: invalidateQueries only *schedules* a
// refetch, so without awaiting, isPending flips false and the form re-arms (or
// a row's buttons re-enable) while the lists still show pre-mutation data.
async function invalidateReceiptViews(
  queryClient: ReturnType<typeof useQueryClient>,
  agreementId: string,
) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['agreement-receipts'] }),
    queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'receipts'] }),
  ])
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
    onSuccess: async () => {
      await invalidateReceiptViews(queryClient, agreementId)
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to create receipt'),
  })
}

export function useVoidReceipt(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (receiptId: string) => agreementReceiptService.void(agreementId, receiptId),
    onSuccess: async () => {
      await invalidateReceiptViews(queryClient, agreementId)
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to void receipt'),
  })
}

export function useApReviewReceipt(agreementId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ receiptId, action }: { receiptId: string; action: ReceiptApReviewAction }) =>
      agreementReceiptService.apReview(agreementId, receiptId, action),
    onSuccess: async () => {
      await invalidateReceiptViews(queryClient, agreementId)
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
// Refreshing both views is handled by invalidateReceiptViews, same as every
// other mutation in this file.

export function useVoidReceiptAny() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ agreementId, receiptId }: { agreementId: string; receiptId: string }) =>
      agreementReceiptService.void(agreementId, receiptId),
    onSuccess: async (_data, { agreementId }) => {
      await invalidateReceiptViews(queryClient, agreementId)
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
      await invalidateReceiptViews(queryClient, agreementId)
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to record AP review'),
  })
}
