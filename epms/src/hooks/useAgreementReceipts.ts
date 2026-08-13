import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  agreementReceiptService,
  type CreateReceiptBody,
  type ReceiptApReviewAction,
  type ReceiptListAllFilters,
  type ReceiptStatus,
  type UpdateReceiptBody,
} from '@/services/agreementReceipts'
import {
  agreementReceiptAttachmentService,
  receiptAttachmentsQueryKey,
} from '@/services/agreementReceiptAttachments'

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

// Single receipt (Task 12) — backs ReceiptDetailPage, whose URL (/receipts/:id)
// carries no agreement_id, so it reads GET /agreement-receipts/{id} rather than
// the agreement-scoped route.
//
// The key sits UNDER ['agreement-receipts'] on purpose: that is exactly the
// prefix invalidateReceiptViews already invalidates, so the detail view is
// refreshed by every receipt mutation for free and cannot become a third view
// somebody forgets to refresh. 'detail' distinguishes it from
// useAllReceipts' ['agreement-receipts', filters] — an object never collides
// with that string.
export function useReceipt(receiptId: string) {
  return useQuery({
    queryKey: ['agreement-receipts', 'detail', receiptId],
    queryFn: () => agreementReceiptService.get(receiptId),
    enabled: Boolean(receiptId),
  })
}

// Attachment metadata for one receipt. Same key ReceiptTable's per-row cell
// uses (receiptAttachmentsQueryKey, now in services/agreementReceiptAttachments.ts)
// so a photo uploaded from the detail page shows up in the agreement's table
// too. No staleTime here, unlike that cell: this page can add and remove
// photos, so its list genuinely changes while it is open.
export function useReceiptAttachments(agreementId: string, receiptId: string) {
  return useQuery({
    queryKey: receiptAttachmentsQueryKey(agreementId, receiptId),
    queryFn: () => agreementReceiptAttachmentService.list(agreementId, receiptId),
    enabled: Boolean(agreementId && receiptId),
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
// There is a THIRD view — the invoice detail page's reconciliation panel
// (useInvoiceAgreementReceipts). It is not listed here because it is keyed
// under ['agreement-receipts', 'for-invoice', …] and so is covered by the
// first entry below. It was NOT always: it used to live under ['invoices', …],
// which nothing here matched, and a removed receipt kept being offered as
// claimable on that panel until a hard reload — the user hit the resulting
// raw-UUID 422 on a service sign-off. Adding a view to this file is the wrong
// instinct; putting its key under a prefix already invalidated here is the
// right one, because it cannot be forgotten.
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
export async function invalidateReceiptViews(
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
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to remove receipt'),
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
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to remove receipt'),
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

// ─── Task 12: edit + attachments, from the detail page ────────────────────
//
// PATCH and the attachment routes existed since Task 3 with NOTHING calling
// them (whole-branch review I3). Their absence had a concrete cost: when
// ReceiptEntryForm's photo upload AND its compensating PATCH both fail, the
// receipt sits `open`, with no photo and no reason — a payable document with
// no evidence — and voiding + re-keying it was the only way out.

export function useUpdateReceiptAny() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ agreementId, receiptId, body }: { agreementId: string; receiptId: string; body: UpdateReceiptBody }) =>
      agreementReceiptService.update(agreementId, receiptId, body),
    onSuccess: async (_data, { agreementId }) => {
      await invalidateReceiptViews(queryClient, agreementId)
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to save receipt'),
  })
}

// Both attachment mutations refresh TWO things: the attachment list itself,
// and the receipt views — `attachment_count` is a field ON the receipt payload
// (crud/agreement_receipt.py's correlated subquery), so the list page's
// "No photo" warning and this page's own header would otherwise keep showing
// the pre-upload count. Same load-bearing await as everywhere else in this
// file: without it the buttons re-arm before the refetch lands.
export function useUploadReceiptAttachment() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ agreementId, receiptId, file }: { agreementId: string; receiptId: string; file: File }) =>
      agreementReceiptAttachmentService.upload(agreementId, receiptId, file),
    onSuccess: async (_data, { agreementId, receiptId }) => {
      await queryClient.invalidateQueries({ queryKey: receiptAttachmentsQueryKey(agreementId, receiptId) })
      await invalidateReceiptViews(queryClient, agreementId)
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to upload photo'),
  })
}

export function useDeleteReceiptAttachment() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ agreementId, receiptId, attachmentId }: { agreementId: string; receiptId: string; attachmentId: string }) =>
      agreementReceiptAttachmentService.delete(agreementId, receiptId, attachmentId),
    onSuccess: async (_data, { agreementId, receiptId }) => {
      await queryClient.invalidateQueries({ queryKey: receiptAttachmentsQueryKey(agreementId, receiptId) })
      await invalidateReceiptViews(queryClient, agreementId)
    },
    onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Failed to delete photo'),
  })
}
