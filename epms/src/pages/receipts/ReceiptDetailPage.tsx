import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  ArrowLeft, Ban, CheckCircle2, XCircle, Paperclip, Upload, X,
  AlertTriangle, Lock, ExternalLink,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { cn, formatAmount, formatDate, formatDateTime } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth.store'
import { useRolePermissions } from '@/hooks/useConfig'
import { useUserDirectory } from '@/hooks/useUsers'
import {
  useReceipt,
  useReceiptAttachments,
  useUpdateReceiptAny,
  useUploadReceiptAttachment,
  useDeleteReceiptAttachment,
  useVoidReceiptAny,
  useApReviewReceiptAny,
} from '@/hooks/useAgreementReceipts'
import { VOIDABLE_STATUSES } from '@/components/agreements/ReceiptTable'
import {
  ReceiptVendorPicker,
  type ReceiptVendorValue,
} from '@/components/agreements/ReceiptVendorPicker'
import { agreementReceiptAttachmentService } from '@/services/agreementReceiptAttachments'
import {
  EDITABLE_STATUSES,
  RECEIPT_TYPE_LABELS,
  receiptTotalsMatch,
  type ApiReceiptWithAgreement,
  type ReceiptType,
} from '@/services/agreementReceipts'
import type { DocumentStatus } from '@/types'

const TYPE_OPTIONS: ReceiptType[] = ['counter_slip', 'delivery', 'service']

// Why the edit form is locked, in the receipt's own terms. Never just a
// greyed-out button: a recorder who can't tell whether the receipt is locked,
// their permission is missing, or the page is broken will re-key the whole
// receipt (or call AP) instead.
function lockReason(receipt: ApiReceiptWithAgreement): string {
  switch (receipt.status) {
    case 'reconciled':
      return (
        `This receipt has been claimed by invoice ${receipt.invoice_ref ?? 'it was matched against'} ` +
        'and can no longer be edited — editing it would leave that invoice matched against ' +
        'figures that no longer exist. Detach or re-match the invoice to release the receipt ' +
        'back to "open".'
      )
    case 'rejected':
      return (
        'AP rejected this receipt. A rejected receipt is final — it cannot be edited, approved, ' +
        'or removed. Record a new receipt if this spend still needs to be paid.'
      )
    case 'voided':
      return 'This receipt was removed. A removed receipt is final — record a new receipt instead.'
    default:
      return 'This receipt cannot be edited in its current state.'
  }
}

// ─── Page ─────────────────────────────────────────────────────────────────────

// Page skeleton (back arrow + title row, card sections, right-hand info column,
// action buttons top-right) copied from pages/gr/GrDetailPage.tsx — the shape
// every other document detail page in EPMS already has.
export default function ReceiptDetailPage() {
  const { id = '' } = useParams<{ id: string }>()
  const { data: receipt, isLoading, isError, error } = useReceipt(id)

  const { user } = useAuthStore()
  const perms = useRolePermissions().data?.permissions
  // Same two keys ReceiptListPage gates its row actions on, which are the same
  // keys the backend enforces: epms.agreement.receipt.write on PATCH / DELETE /
  // attachment upload+delete, epms.invoice.match on ap-review.
  const canRecordReceipt = user?.role === 'system_admin' || !!perms?.['epms.agreement.receipt.write']
  const canApReview = user?.role === 'system_admin' || !!perms?.['epms.invoice.match']

  if (isLoading) return <div className="p-8 text-center text-neutral-400">Loading…</div>

  if (isError || !receipt) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-lg font-semibold text-neutral-500">Receipt not found</p>
        {isError && (
          <p className="mt-2 text-sm text-neutral-400">
            {error instanceof Error ? error.message : 'It may have been removed.'}
          </p>
        )}
        <Link to="/receipts" className="mt-4 text-sm text-primary-600 hover:underline">
          ← Back to Agreement Receipts
        </Link>
      </div>
    )
  }

  return (
    <ReceiptDetail
      // key: a different receipt id remounts, so no stale form state can
      // survive navigating from one receipt to another within this route.
      key={receipt.id}
      receipt={receipt}
      canRecordReceipt={canRecordReceipt}
      canApReview={canApReview}
    />
  )
}

// Split out so the form's useState can be seeded from a receipt that is
// guaranteed to exist (hooks cannot live behind the loading/404 early returns
// above), and so remounting on a different receipt id resets the form.
function ReceiptDetail({
  receipt, canRecordReceipt, canApReview,
}: {
  receipt: ApiReceiptWithAgreement
  canRecordReceipt: boolean
  canApReview: boolean
}) {
  const { data: directory } = useUserDirectory()
  const users = directory?.items ?? []
  // received_by / created_by / ap_reviewed_by are bare user UUIDs on the wire —
  // resolved to names here; a raw UUID is never rendered.
  const userNames = useMemo(() => {
    const map = new Map<string, string>()
    for (const u of users) map.set(u.id, u.full_name)
    return map
  }, [users])

  const agreementId = receipt.agreement_id
  const isEditable = EDITABLE_STATUSES.has(receipt.status)
  const canEdit = canRecordReceipt && isEditable
  const canVoid = canRecordReceipt && VOIDABLE_STATUSES.has(receipt.status)
  const canReview = canApReview && receipt.status === 'pending_ap_review'

  const updateReceipt = useUpdateReceiptAny()
  const voidReceipt = useVoidReceiptAny()
  const apReview = useApReviewReceiptAny()
  const [pendingReview, setPendingReview] = useState<'approve' | 'reject' | null>(null)
  const anyPending = updateReceipt.isPending || voidReceipt.isPending || apReview.isPending

  // ── Edit form state ──────────────────────────────────────────────────────
  const [receiptType, setReceiptType] = useState<ReceiptType>(receipt.receipt_type)
  const [receiptDate, setReceiptDate] = useState(receipt.receipt_date)
  const [receiptRef, setReceiptRef] = useState(receipt.receipt_ref ?? '')
  // The merchant on the slip, as the pair it really is (Task 14): the
  // vendor-master row it is bound to, and the text stored either way. Editable
  // alongside the other recorded facts — a vendor keyed wrong, bound to the
  // wrong row, or left blank because OCR couldn't read the header is exactly
  // the case the mismatch note below asks the reader to check. Binding it here
  // is also how a receipt recorded before this existed gets its id.
  const [vendor, setVendor] = useState<ReceiptVendorValue>({
    vendorId: receipt.vendor_id, vendorName: receipt.vendor_name ?? '',
  })
  const [amount, setAmount] = useState(receipt.amount)
  const [taxAmount, setTaxAmount] = useState(receipt.tax_amount)
  const [totalAmount, setTotalAmount] = useState(receipt.total_amount)
  const [receivedBy, setReceivedBy] = useState(receipt.received_by)
  const [missingReason, setMissingReason] = useState(receipt.missing_receipt_reason ?? '')
  const [notes, setNotes] = useState(receipt.notes ?? '')
  const [formError, setFormError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<string | null>(null)

  // Re-seed the form from the server after a save (and after any refetch that
  // changes the row), but NOT while an edit is in progress: a background
  // refetch — e.g. the one an attachment upload triggers — must not wipe what
  // the user is typing. `dirty` is cleared on every successful save, so the
  // post-save refetch is exactly the case this lets through.
  const dirty = useRef(false)
  useEffect(() => {
    if (dirty.current) return
    setReceiptType(receipt.receipt_type)
    setReceiptDate(receipt.receipt_date)
    setReceiptRef(receipt.receipt_ref ?? '')
    setVendor({ vendorId: receipt.vendor_id, vendorName: receipt.vendor_name ?? '' })
    setAmount(receipt.amount)
    setTaxAmount(receipt.tax_amount)
    setTotalAmount(receipt.total_amount)
    setReceivedBy(receipt.received_by)
    setMissingReason(receipt.missing_receipt_reason ?? '')
    setNotes(receipt.notes ?? '')
  }, [receipt])

  const touch = <T,>(setter: (v: T) => void) => (v: T) => { dirty.current = true; setter(v) }

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault()
    setSavedAt(null)
    // Amounts arrive as strings (Pydantic serialises Decimal as JSON string)
    // and the inputs hold strings too — every comparison below coerces
    // explicitly rather than relying on JS's implicit rules.
    const amt = Number(amount)
    const tax = Number(taxAmount)
    const tot = Number(totalAmount)
    if (!receiptDate) {
      setFormError('Receipt date is required')
      return
    }
    if (!receivedBy) {
      setFormError('Received by is required')
      return
    }
    if (amount === '' || taxAmount === '' || totalAmount === ''
        || Number.isNaN(amt) || Number.isNaN(tax) || Number.isNaN(tot)) {
      setFormError('Amount, tax and total are all required — enter 0 for tax if the receipt shows none')
      return
    }
    // Cent-integer comparison, matching the backend's exact Decimal equality
    // (see receiptTotalsMatch) — a float tolerance would let through values the
    // API then rejects with a 422.
    if (!receiptTotalsMatch(tot, amt, tax)) {
      setFormError('Total must equal amount + tax')
      return
    }
    setFormError(null)
    // mutate, not mutateAsync: the hook's onError already alerts, and
    // mutateAsync would ALSO reject — an unhandled promise rejection out of
    // this submit handler on every failed save.
    updateReceipt.mutate({
      agreementId,
      receiptId: receipt.id,
      // Field names are the backend's ReceiptUpdate fields verbatim.
      body: {
        receipt_type: receiptType,
        receipt_date: receiptDate,
        receipt_ref: receiptRef.trim() || null,
        // Both halves, always. vendor_name is a snapshot of the bound row, so
        // sending one without the other is how an id and a name end up naming
        // two different merchants. null unbinds (the server leaves the text
        // alone in that case).
        vendor_id: vendor.vendorId,
        vendor_name: vendor.vendorName.trim() || null,
        amount: amt,
        tax_amount: tax,
        total_amount: tot,
        received_by: receivedBy,
        missing_receipt_reason: missingReason.trim() || null,
        notes: notes.trim() || null,
      },
    }, {
      onSuccess: () => {
        dirty.current = false
        setSavedAt(new Date().toLocaleTimeString())
      },
    })
  }

  const handleVoid = () => {
    if (!confirm(
      'Remove this receipt? This cannot be undone — a removed receipt is final and can never be ' +
      'edited, approved, or claimed by an invoice. Record a new receipt if the spend still needs paying.'
    )) return
    voidReceipt.mutate({ agreementId, receiptId: receipt.id })
  }

  const handleReview = (action: 'approve' | 'reject') => {
    // Reject has no way back: the backend writes status = "rejected", which is
    // in RETIRED (neither EDITABLE nor VOIDABLE), and ap_review only accepts
    // FROM pending_ap_review — no endpoint can move a rejected receipt
    // anywhere. Approve is deliberately NOT confirmed: it is the high-frequency
    // action and it lands on `open`, which is still editable and voidable.
    if (action === 'reject' && !confirm(
      'Reject this receipt? This is final — a rejected receipt can never be approved, removed, or ' +
      'edited afterward. The only way to record this spend again is to enter a brand-new receipt.'
    )) return
    setPendingReview(action)
    apReview.mutate({ agreementId, receiptId: receipt.id, action }, {
      onSettled: () => setPendingReview(null),
    })
  }

  // receipt_ref is optional, so the title falls back to something a human can
  // still recognise (type + date) rather than the row's UUID.
  const heading = receipt.receipt_ref
    ? receipt.receipt_ref
    : `${RECEIPT_TYPE_LABELS[receipt.receipt_type]} · ${formatDate(receipt.receipt_date)}`

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <Link to="/receipts" className="mt-1 text-neutral-400 hover:text-neutral-600 transition-colors">
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-neutral-900 font-mono">{heading}</h1>
              <StatusBadge status={receipt.status as DocumentStatus} />
            </div>
            <p className="mt-1 text-sm text-neutral-500">
              {RECEIPT_TYPE_LABELS[receipt.receipt_type]} · {receipt.agreement_number} ·{' '}
              {formatAmount(Number(receipt.total_amount), receipt.currency)}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {canReview && (
            <>
              <Button
                variant="success-outline"
                className="gap-2"
                onClick={() => handleReview('approve')}
                disabled={anyPending}
              >
                <CheckCircle2 className="h-4 w-4" />
                {pendingReview === 'approve' ? 'Working…' : 'Approve'}
              </Button>
              <Button
                variant="secondary"
                className="gap-2"
                onClick={() => handleReview('reject')}
                disabled={anyPending}
              >
                <XCircle className="h-4 w-4" />
                {pendingReview === 'reject' ? 'Working…' : 'Reject'}
              </Button>
            </>
          )}
          {canVoid && (
            <Button
              variant="secondary"
              className="gap-2 text-danger-600 hover:text-danger-700"
              onClick={handleVoid}
              disabled={anyPending}
            >
              <Ban className="h-4 w-4" />
              {voidReceipt.isPending ? 'Working…' : 'Remove'}
            </Button>
          )}
        </div>
      </div>

      {/* Why this receipt is in AP review — the same fact ReceiptListPage shows
          on its sub-row, repeated here because this page is now a place AP can
          decide from. */}
      {receipt.status === 'pending_ap_review' && (
        <div className="rounded-lg border border-warning-200 bg-warning-50 p-4 flex gap-3">
          <AlertTriangle className="h-5 w-5 text-warning-500 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-warning-700">Waiting for AP review</p>
            <p className="text-sm text-warning-700/90 mt-0.5">
              <span className="font-medium">Reason recorded: </span>
              {receipt.missing_receipt_reason?.trim()
                ? receipt.missing_receipt_reason
                : <span className="italic">none — this receipt was moved here by hand.</span>}
            </p>
          </div>
        </div>
      )}

      <div className="flex gap-6 items-start">
        {/* Main column */}
        <div className="flex-1 min-w-0 flex flex-col gap-4">
          {/* Receipt details — editable form, or read-only with the reason */}
          <div className="rounded-xl border border-neutral-200 bg-white p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
                Receipt Details
              </h2>
              {savedAt && (
                <span className="text-xs text-success-600">Saved at {savedAt}</span>
              )}
            </div>

            {!isEditable && (
              <div className="mb-5 flex gap-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                <Lock className="h-4 w-4 text-neutral-400 flex-shrink-0 mt-0.5" />
                <p className="text-sm text-neutral-600">{lockReason(receipt)}</p>
              </div>
            )}
            {/* A REMINDER, not an error (Task 13): a slip from another trading
                name of the same group is a legal receipt, so nothing here
                blocks editing, approving or matching. The verdict is the
                server's (vendor_mismatch, epms-api is_vendor_mismatch) — never
                recomputed here, so this page and the list can never disagree.
                It names BOTH merchants, because "vendor mismatch" alone gives
                the reader nothing to act on. Rendered in read-only mode too:
                a reconciled receipt is where this matters most and its form
                is locked. */}
            {receipt.vendor_mismatch && (
              <div className="mb-5 flex gap-3 rounded-lg border border-warning-200 bg-warning-50 p-4">
                <AlertTriangle className="h-4 w-4 flex-shrink-0 text-warning-500 mt-0.5" />
                <div className="text-sm text-warning-800">
                  <p className="font-semibold">This receipt is from a different vendor</p>
                  <p className="mt-0.5">
                    The receipt says <span className="font-medium">{receipt.vendor_name}</span>, but
                    agreement {receipt.agreement_number} is with{' '}
                    <span className="font-medium">{receipt.agreement_vendor_name}</span>. Check it was
                    charged to the right house account — or correct the vendor if it was keyed wrong.
                    Different stores of the same group are fine.
                  </p>
                </div>
              </div>
            )}
            {isEditable && !canRecordReceipt && (
              <div className="mb-5 flex gap-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                <Lock className="h-4 w-4 text-neutral-400 flex-shrink-0 mt-0.5" />
                <p className="text-sm text-neutral-600">
                  You can view this receipt but not change it — editing needs the "record agreement
                  receipts" permission. Ask an administrator if you need it.
                </p>
              </div>
            )}

            {canEdit ? (
              <form onSubmit={handleSave} className="flex flex-col gap-5">
                <div className="flex flex-col gap-2">
                  <label className="text-sm font-medium text-neutral-700">Receipt type</label>
                  <div className="flex gap-3">
                    {TYPE_OPTIONS.map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => touch(setReceiptType)(t)}
                        disabled={anyPending}
                        className={cn(
                          'flex-1 rounded-lg border px-4 py-2.5 text-sm font-medium transition-colors',
                          receiptType === t
                            ? 'border-primary-600 bg-primary-50 text-primary-700'
                            : 'border-neutral-300 text-neutral-600 hover:border-neutral-400',
                        )}
                      >
                        {RECEIPT_TYPE_LABELS[t]}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormField label="Receipt date" required htmlFor="receipt-date">
                    <Input
                      id="receipt-date" type="date" value={receiptDate}
                      onChange={(e) => touch(setReceiptDate)(e.target.value)} required
                    />
                  </FormField>
                  <FormField label="Reference #" htmlFor="receipt-ref">
                    {/* DB column is String(64) — an overflowing paste raises a
                        DataError the API's IntegrityError handler doesn't catch
                        (a bare 500). maxLength stops it reaching the request. */}
                    <Input
                      id="receipt-ref" value={receiptRef} maxLength={64}
                      placeholder="Receipt number"
                      onChange={(e) => touch(setReceiptRef)(e.target.value)}
                    />
                  </FormField>
                  <FormField
                    label="Vendor on receipt" htmlFor="receipt-vendor"
                    hint="The merchant printed on the slip. Pick it from the vendor list if it's there — otherwise just type what the slip says, or leave it blank if it isn't legible."
                  >
                    {/* Optional, exactly as on the entry form: an unmatched
                        merchant saves as text and blocks nothing. */}
                    <ReceiptVendorPicker
                      inputId="receipt-vendor"
                      value={vendor}
                      onChange={touch(setVendor)}
                      disabled={anyPending}
                    />
                  </FormField>
                  <FormField label={`Amount before tax (${receipt.currency})`} required htmlFor="receipt-amount">
                    <Input
                      id="receipt-amount" type="number" step="0.01" value={amount}
                      onChange={(e) => touch(setAmount)(e.target.value)}
                    />
                  </FormField>
                  <FormField label={`Tax (${receipt.currency})`} required htmlFor="receipt-tax">
                    <Input
                      id="receipt-tax" type="number" step="0.01" value={taxAmount}
                      onChange={(e) => touch(setTaxAmount)(e.target.value)}
                    />
                  </FormField>
                  <FormField label={`Total (${receipt.currency})`} required htmlFor="receipt-total">
                    <Input
                      id="receipt-total" type="number" step="0.01" value={totalAmount}
                      onChange={(e) => touch(setTotalAmount)(e.target.value)}
                    />
                  </FormField>
                  <FormField
                    label="Received by" required htmlFor="receipt-received-by"
                    hint="The person who brought the receipt in — not a sign-off or approval."
                  >
                    <select
                      id="receipt-received-by"
                      value={receivedBy}
                      onChange={(e) => touch(setReceivedBy)(e.target.value)}
                      className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                    >
                      <option value="">Select…</option>
                      {users.map((u) => (
                        <option key={u.id} value={u.id}>{u.full_name}</option>
                      ))}
                    </select>
                  </FormField>
                </div>

                <FormField
                  label="Reason for missing photo"
                  htmlFor="receipt-missing-reason"
                  hint={
                    receipt.status === 'open'
                      ? 'Writing a reason here sends this receipt to AP review — that is the point: a receipt with no evidence must not be paid unreviewed.'
                      : 'This receipt is already with AP. Rewording the reason will not change that — only AP can approve or reject it.'
                  }
                >
                  <textarea
                    id="receipt-missing-reason"
                    rows={2}
                    value={missingReason}
                    onChange={(e) => touch(setMissingReason)(e.target.value)}
                    placeholder="e.g. receipt was lost / illegible / not issued"
                    className="w-full resize-none rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                  />
                </FormField>

                <FormField label="Notes" htmlFor="receipt-notes">
                  <textarea
                    id="receipt-notes"
                    rows={2}
                    value={notes}
                    onChange={(e) => touch(setNotes)(e.target.value)}
                    placeholder="Optional notes"
                    className="w-full resize-none rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                  />
                </FormField>

                {formError && <p className="text-sm text-danger-600">{formError}</p>}

                <div className="flex justify-end">
                  <Button type="submit" disabled={anyPending} className={cn(anyPending && 'cursor-not-allowed')}>
                    {updateReceipt.isPending ? 'Saving…' : 'Save Changes'}
                  </Button>
                </div>
              </form>
            ) : (
              <div className="grid grid-cols-1 gap-x-8 gap-y-2 sm:grid-cols-2">
                <MetaRow label="Receipt type" value={RECEIPT_TYPE_LABELS[receipt.receipt_type]} />
                <MetaRow label="Receipt date" value={formatDate(receipt.receipt_date)} />
                <MetaRow label="Reference #" value={receipt.receipt_ref ?? '—'} mono />
                {/* Not a plain MetaRow: "Princess Auto, from the vendor list"
                    and "Princess Auto, as typed off a slip" are different
                    facts, and this read-only view is the one a reconciled
                    (locked) receipt shows. Muted, never an error colour —
                    unmatched is normal. */}
                <div className="flex gap-2 text-sm">
                  <span className="text-neutral-400 min-w-36 flex-shrink-0">Vendor on receipt</span>
                  <span className="text-neutral-800 font-medium break-words">
                    {receipt.vendor_name ?? '—'}
                    {receipt.vendor_name && !receipt.vendor_matched && (
                      <span className="ml-2 rounded-full bg-neutral-100 px-1.5 py-0.5 text-[10px] font-medium text-neutral-500">
                        not in vendor list
                      </span>
                    )}
                  </span>
                </div>
                <MetaRow label="Received by" value={userNames.get(receipt.received_by) ?? '—'} />
                <MetaRow label="Amount before tax" value={formatAmount(Number(receipt.amount), receipt.currency)} mono />
                <MetaRow label="Tax" value={formatAmount(Number(receipt.tax_amount), receipt.currency)} mono />
                <MetaRow label="Total" value={formatAmount(Number(receipt.total_amount), receipt.currency)} mono />
                <MetaRow label="Reason for missing photo" value={receipt.missing_receipt_reason ?? '—'} />
                <MetaRow label="Notes" value={receipt.notes ?? '—'} />
              </div>
            )}
          </div>

          {/* Photos / proof files */}
          <ReceiptAttachmentsCard
            agreementId={agreementId}
            receipt={receipt}
            canWrite={canRecordReceipt}
          />
        </div>

        {/* Right column — context that isn't editable here */}
        <div className="w-72 flex-shrink-0 flex flex-col gap-4">
          <div className="rounded-xl border border-neutral-200 bg-white p-5">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">
              Linked Documents
            </h3>
            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between rounded-lg border border-neutral-200 px-3 py-2.5">
                <div className="min-w-0">
                  <p className="text-xs text-neutral-400">Agreement</p>
                  {/* Never the bare agreement_id — the human number. */}
                  <Link to={`/agreements/${agreementId}`} className="text-sm font-medium text-primary-600 hover:underline font-mono">
                    {receipt.agreement_number}
                  </Link>
                </div>
                <Link to={`/agreements/${agreementId}`}>
                  <ExternalLink className="h-4 w-4 text-neutral-400 hover:text-primary-600" />
                </Link>
              </div>
              {receipt.invoice_id ? (
                <div className="flex items-center justify-between rounded-lg border border-neutral-200 px-3 py-2.5">
                  <div className="min-w-0">
                    <p className="text-xs text-neutral-400">Claimed by invoice</p>
                    <Link to={`/invoices/${receipt.invoice_id}`} className="text-sm font-medium text-primary-600 hover:underline font-mono">
                      {/* invoice_ref is resolved server-side; the short id slice
                          is only a fallback for a row whose invoice has no
                          internal_ref yet — same convention as InvoiceDetailPage. */}
                      {receipt.invoice_ref ?? receipt.invoice_id.slice(0, 8)}
                    </Link>
                  </div>
                  <Link to={`/invoices/${receipt.invoice_id}`}>
                    <ExternalLink className="h-4 w-4 text-neutral-400 hover:text-primary-600" />
                  </Link>
                </div>
              ) : (
                <p className="text-xs text-neutral-400">Not claimed by any invoice yet.</p>
              )}
            </div>
          </div>

          <div className="rounded-xl border border-neutral-200 bg-white p-5">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">
              Record
            </h3>
            <div className="flex flex-col gap-2">
              <MetaRow label="Recorded by" value={userNames.get(receipt.created_by) ?? '—'} />
              <MetaRow label="Recorded on" value={formatDateTime(receipt.created_at)} />
              {receipt.ap_reviewed_at ? (
                <>
                  <MetaRow
                    label="AP decision"
                    value={receipt.status === 'rejected' ? 'Rejected' : 'Approved'}
                  />
                  <MetaRow
                    label="Reviewed by"
                    value={receipt.ap_reviewed_by ? (userNames.get(receipt.ap_reviewed_by) ?? '—') : '—'}
                  />
                  <MetaRow label="Reviewed on" value={formatDateTime(receipt.ap_reviewed_at)} />
                </>
              ) : (
                <MetaRow label="AP review" value="Not reviewed" />
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Attachments ──────────────────────────────────────────────────────────────

function ReceiptAttachmentsCard({
  agreementId, receipt, canWrite,
}: {
  agreementId: string
  receipt: ApiReceiptWithAgreement
  canWrite: boolean
}) {
  const { data: attachments = [], isLoading } = useReceiptAttachments(agreementId, receipt.id)
  const upload = useUploadReceiptAttachment()
  const remove = useDeleteReceiptAttachment()
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const busy = upload.isPending || remove.isPending

  const handlePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    upload.mutate({ agreementId, receiptId: receipt.id, file })
  }

  const handleDelete = (attachmentId: string, filename: string) => {
    // Wording escalates for a claimed receipt: this file is the evidence
    // behind an invoice that may already be paid. The backend allows the
    // delete (the attachment routes gate on permission, not receipt status),
    // so the honest thing is to say what it costs rather than hide the button.
    const claimed = receipt.status === 'reconciled'
    if (!confirm(
      `Delete ${filename}?` +
      (claimed
        ? ` This receipt has already been claimed by invoice ${receipt.invoice_ref ?? '(matched)'} — ` +
          'this file is the evidence behind that payment. Deleting it cannot be undone.'
        : ' This cannot be undone.')
    )) return
    setPendingDeleteId(attachmentId)
    remove.mutate({ agreementId, receiptId: receipt.id, attachmentId }, {
      onSettled: () => setPendingDeleteId(null),
    })
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
          Photos &amp; Proof Files
        </h2>
        {canWrite && (
          <label
            htmlFor="receipt-attachment-upload"
            className={cn(
              'inline-flex items-center gap-2 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700',
              busy ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:border-primary-400 hover:text-primary-700',
            )}
          >
            <Upload className="h-4 w-4" />
            {upload.isPending ? 'Uploading…' : 'Add photo'}
            <input
              id="receipt-attachment-upload"
              type="file"
              accept="image/*,.pdf"
              className="sr-only"
              disabled={busy}
              onChange={handlePick}
            />
          </label>
        )}
      </div>

      {isLoading ? (
        <p className="py-6 text-center text-sm text-neutral-400">Loading…</p>
      ) : attachments.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
          <AlertTriangle className="h-7 w-7 text-warning-500" />
          <p className="text-sm font-medium text-warning-700">No photo or proof file on this receipt</p>
          <p className="max-w-md text-xs text-neutral-500">
            {canWrite
              ? 'A receipt with no evidence can still be claimed by an invoice and paid. Add the photo here, or write a reason above so AP reviews it before payment.'
              : 'A receipt with no evidence can still be claimed by an invoice and paid. Ask whoever recorded it to attach the photo.'}
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {attachments.map((att) => (
            <div key={att.id} className="flex items-center gap-3 rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3">
              <Paperclip className="h-4 w-4 shrink-0 text-neutral-400" />
              <span className="flex-1 truncate text-sm text-neutral-700" title={att.filename}>{att.filename}</span>
              <span className="text-xs text-neutral-400">{(att.file_size / 1024).toFixed(0)} KB</span>
              <button
                type="button"
                // Token-bearing blob fetch, never a bare <a href>: an
                // unauthenticated request to the download route is 401'd and
                // nginx's SPA fallback then serves the app shell, which reads
                // as "Download bounces me to the homepage". Errors are surfaced
                // — download() rejects rather than failing silently.
                onClick={() => agreementReceiptAttachmentService
                  .download(agreementId, receipt.id, att.id, att.filename)
                  .catch((err: unknown) => alert(err instanceof Error ? err.message : 'Download failed'))}
                className="text-xs text-primary-600 hover:underline"
              >
                Download
              </button>
              {canWrite && (
                <button
                  type="button"
                  onClick={() => handleDelete(att.id, att.filename)}
                  disabled={busy}
                  title="Delete this file"
                  className={cn(
                    'text-neutral-300 hover:text-danger-500',
                    busy && 'cursor-not-allowed opacity-50',
                  )}
                >
                  {pendingDeleteId === att.id
                    ? <span className="text-xs text-neutral-500">Deleting…</span>
                    : <X className="h-3.5 w-3.5" />}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function MetaRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="text-neutral-400 min-w-36 flex-shrink-0">{label}</span>
      <span className={cn('text-neutral-800 font-medium break-words', mono && 'font-mono text-xs')}>{value}</span>
    </div>
  )
}
