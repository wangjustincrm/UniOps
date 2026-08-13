import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Upload, Loader2, Info, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { cn, todayISODate } from '@/lib/utils'
import { useUserDirectory } from '@/hooks/useUsers'
import { useCreateReceipt, invalidateReceiptViews } from '@/hooks/useAgreementReceipts'
import { agreementReceiptAttachmentService, receiptAttachmentsQueryKey } from '@/services/agreementReceiptAttachments'
import { agreementReceiptService, ocrService, receiptTotalsMatch, type ReceiptType } from '@/services/agreementReceipts'
import {
  ReceiptVendorPicker,
  type ReceiptVendorSuggestion,
  type ReceiptVendorValue,
} from '@/components/agreements/ReceiptVendorPicker'

interface ReceiptEntryFormProps {
  agreementId: string
  // Chosen by the caller — ReceiptCreatePage's type selector (Task 10). Defaults
  // to 'counter_slip' to match the backend's own default (schemas/agreement_receipt.py
  // ReceiptCreate.receipt_type) for the sake of any future embedding that omits it.
  receiptType?: ReceiptType
  // Fires once the receipt row itself exists — i.e. at the same point
  // resetForm() already ran unconditionally, regardless of whether the photo
  // attachment leg (below) succeeded, failed-but-compensated, or failed
  // entirely. Task 10 fix round 1: without this, ReceiptCreatePage's only
  // feedback after a successful submit was the form quietly clearing itself —
  // visually identical to a failed submit. Optional so this component still
  // works standalone with no caller-visible behavior change.
  onSuccess?: () => void
}

// Entry flow: pick a photo → auto OCR → prefill (still fully editable) →
// fill in received_by → submit the row → upload the photo as an attachment
// against the receipt id just returned. Two separate API calls, same pattern as
// PrCreatePage (create doc, then upload attachments against the new id) —
// there is no single create-with-attachment endpoint.
// A counter slip is a scanned document the system reads: the photo IS the
// record, OCR fills the form from it, and a slip with no photo is an exception
// that AP has to review. A delivery note or a service sign-off is not that —
// it is somebody confirming that goods or a service arrived. There may be a
// signed PDF worth keeping, there may not be, and either way nobody is
// scanning a work order for its totals. So the elaborate photo-and-OCR block
// is the counter-slip's alone; the other two get a plain optional attachment.
const TYPE_COPY = {
  counter_slip: {
    dateLabel: 'Receipt date',
    refLabel: 'Reference #',
    refPlaceholder: 'Receipt number',
    byLabel: 'Picked up by',
    byHint: 'The person who brought the receipt in — not a sign-off or approval.',
  },
  delivery: {
    dateLabel: 'Delivery date',
    refLabel: 'Delivery note #',
    refPlaceholder: 'Delivery note number',
    byLabel: 'Received by',
    byHint: 'The person who took delivery.',
  },
  service: {
    dateLabel: 'Service date',
    refLabel: 'Work order #',
    refPlaceholder: 'Work order number',
    byLabel: 'Signed off by',
    byHint: 'The person who confirmed the service was performed.',
  },
} as const

export function ReceiptEntryForm({ agreementId, receiptType = 'counter_slip', onSuccess }: ReceiptEntryFormProps) {
  // Only a counter slip runs OCR, demands a photo-or-reason, and routes to AP
  // review when neither is there.
  const isSlip = receiptType === 'counter_slip'
  const copy = TYPE_COPY[receiptType]
  const { data: usersData } = useUserDirectory()
  const users = usersData?.items ?? []
  const createReceipt = useCreateReceipt(agreementId)
  const queryClient = useQueryClient()

  const [file, setFile] = useState<File | null>(null)
  const [ocrState, setOcrState] = useState<'idle' | 'loading' | 'success' | 'failed'>('idle')

  const [receiptDate, setReceiptDate] = useState(todayISODate())
  const [receiptRef, setReceiptRef] = useState('')
  // The merchant on the slip, as the pair it really is: the vendor-master row
  // it matched (when it matched one) and the text stored either way. OCR
  // prefills it and the picker tries to bind it; both halves stay editable.
  // Nothing is compared against the agreement's vendor here — the backend
  // decides that (receipt_vendor_mismatch) and the receipt list/detail views
  // surface it, so there is exactly one verdict in the system.
  const [vendor, setVendor] = useState<ReceiptVendorValue>({ vendorId: null, vendorName: '' })
  // Handed to the picker when OCR reads a merchant name off the photo. A new
  // object each time, so re-reading a photo of the SAME shop re-runs the match.
  const [vendorSuggestion, setVendorSuggestion] = useState<ReceiptVendorSuggestion | null>(null)
  // Bumped by resetForm to REMOUNT the vendor picker (same idiom
  // ReceiptDetailPage uses to reset itself on a different receipt id). The
  // picker holds state this form cannot see — the search term it last sent,
  // and which value came from OCR — and clearing only the value it exposes
  // would leave the next receipt's blank field showing the previous
  // receipt's "AI extracted: …" line and searching for the previous
  // receipt's merchant.
  const [vendorPickerNonce, setVendorPickerNonce] = useState(0)
  const [amount, setAmount] = useState('')
  const [taxAmount, setTaxAmount] = useState('')
  const [totalAmount, setTotalAmount] = useState('')
  const [receivedBy, setReceivedBy] = useState('')
  const [missingReceiptReason, setMissingReceiptReason] = useState('')
  const [notes, setNotes] = useState('')

  const [amountError, setAmountError] = useState<string | null>(null)
  const [receivedByError, setReceivedByError] = useState<string | null>(null)
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [isUploadingAttachment, setIsUploadingAttachment] = useState(false)

  const resetForm = () => {
    setFile(null)
    setOcrState('idle')
    setReceiptDate(todayISODate())
    setReceiptRef('')
    setVendor({ vendorId: null, vendorName: '' })
    setVendorSuggestion(null)
    setVendorPickerNonce((n) => n + 1)
    setAmount('')
    setTaxAmount('')
    setTotalAmount('')
    setReceivedBy('')
    setMissingReceiptReason('')
    setNotes('')
    setAmountError(null)
    setReceivedByError(null)
    setReasonError(null)
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0]
    e.target.value = ''
    if (!picked) return
    setFile(picked)
    // Nothing to read: a delivery note or a work order is a confirmation, not
    // a priced document, and running the slip extractor over it would only
    // produce fields to un-fill.
    if (!isSlip) return
    setOcrState('loading')
    try {
      const fields = await ocrService.receipt(picked)
      setOcrState('success')
      // `slip_ref`, not `receipt_ref` — expense-api's extract_slip() key. The
      // old name here never existed on the wire, so it read as `undefined` and
      // silently prefilled nothing (fixed in Task 13; see OcrReceiptFields).
      if (fields.slip_ref) setReceiptRef(fields.slip_ref)
      // The picker takes it from here: it searches the vendor list for this
      // name and binds it when exactly one vendor is unmistakably that
      // merchant, otherwise leaves the text in place with the candidates open.
      if (fields.vendor_name) setVendorSuggestion({ text: fields.vendor_name })
      if (fields.date) setReceiptDate(fields.date)
      if (fields.amount !== null) setAmount(String(fields.amount))
      if (fields.tax_amount !== null) setTaxAmount(String(fields.tax_amount))
      if (fields.total_amount !== null) setTotalAmount(String(fields.total_amount))
    } catch {
      // OCR failing (incl. a 422 the extractor can't parse) is a normal path,
      // not an error condition — degrade silently to manual entry rather than
      // popping an error dialog for something the user didn't do wrong.
      setOcrState('failed')
    }
  }

  const clearFile = () => {
    setFile(null)
    setOcrState('idle')
  }

  const isPending = createReceipt.isPending || isUploadingAttachment

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    let hasError = false
    if (!receivedBy) {
      setReceivedByError('Required')
      hasError = true
    } else {
      setReceivedByError(null)
    }
    if (isSlip && !file && !missingReceiptReason.trim()) {
      setReasonError('Required when no photo is attached')
      hasError = true
    } else {
      setReasonError(null)
    }
    const amt = Number(amount)
    const tax = Number(taxAmount)
    const tot = Number(totalAmount)
    // Money is a counter-slip concern. A delivery note or a service sign-off
    // records that something arrived, not what it cost — there is no figure
    // printed on it to key in, and the invoice reconciles those types by
    // their existence, not by a total (see InvoiceReceiptsPanel).
    if (!isSlip) {
      setAmountError(null)
    } else if (amount === '' || taxAmount === '' || totalAmount === '' || Number.isNaN(amt) || Number.isNaN(tax) || Number.isNaN(tot)) {
      setAmountError('Amount, tax and total are all required — enter 0 for tax if the receipt shows none')
      hasError = true
    } else if (!receiptTotalsMatch(tot, amt, tax)) {
      setAmountError('Total must equal amount + tax')
      hasError = true
    } else {
      setAmountError(null)
    }
    if (!receiptDate) hasError = true
    if (hasError) return

    const receipt = await createReceipt.mutateAsync({
      receipt_type: receiptType,
      receipt_date: receiptDate,
      receipt_ref: receiptRef.trim() || undefined,
      // Vendor, like the amounts, is a counter-slip question: a slip can come
      // from any merchant and be filed against the wrong house account, which
      // is what the mismatch check exists to catch. A delivery note against
      // an agreement comes from that agreement's supplier by construction —
      // asking again only invites a wrong answer. Omitted entirely (an empty
      // vendor is never reported as a mismatch — schemas/agreement_receipt.py).
      vendor_id: isSlip ? vendor.vendorId ?? undefined : undefined,
      vendor_name: isSlip ? vendor.vendorName.trim() || undefined : undefined,
      // Omitted, not zeroed — 0 is a value the reconciliation would sum.
      amount: isSlip ? amt : undefined,
      tax_amount: isSlip ? tax : undefined,
      total_amount: isSlip ? tot : undefined,
      received_by: receivedBy,
      // Only a counter slip can be "missing" its evidence — the attachment is
      // optional on the other two, so there is nothing to explain and nothing
      // for AP to review (crud/agreement_receipt.py routes to
      // pending_ap_review on this field alone).
      missing_receipt_reason: isSlip && !file ? missingReceiptReason.trim() || undefined : undefined,
      notes: notes.trim() || undefined,
    })

    if (file) {
      setIsUploadingAttachment(true)
      try {
        await agreementReceiptAttachmentService.upload(agreementId, receipt.id, file)
        // Load-bearing await, same rationale as useCreateReceipt/useVoidReceipt's
        // onSuccess: without it, ReceiptTable's attachments cell (staleTime 5min,
        // see ReceiptTable.tsx) can already have cached the empty pre-upload
        // list by the time this resolves, and nothing else was ever going to
        // ask it to refetch — before this fix the row silently showed "—"
        // for up to 5 minutes after a successful upload. Un-awaited, the
        // invalidate is only *scheduled*, so this component could finish
        // (and a fast re-render of ReceiptTable could re-read cache) before the
        // refetch actually lands.
        await queryClient.invalidateQueries({ queryKey: receiptAttachmentsQueryKey(agreementId, receipt.id) })
        // AND the receipt views (fix round 1, Important). `attachment_count`
        // is a field on the RECEIPT payload — a correlated subquery in
        // crud/agreement_receipt.py, not part of the attachment list — so the
        // key above does not carry it. useCreateReceipt's onSuccess already
        // invalidated the receipt views, but that ran BEFORE this upload, when
        // the count really was 0. Without this second pass, an
        // already-mounted Agreement Receipts tab (SPA tab switching fires no
        // window focus, so nothing refetches on its own) keeps showing the
        // amber "⚠ No photo" warning on a receipt whose photo did upload —
        // and AP chases evidence that is already there. The catch branch below
        // has always done this; the success branch was the one missing it.
        await invalidateReceiptViews(queryClient, agreementId)
      } catch (uploadErr) {
        // On a delivery note or a service sign-off the attachment was OPTIONAL:
        // a receipt with none is a complete, valid record, so a failed upload
        // is a failed file transfer and nothing more. Compensating it into AP
        // review — as the counter-slip path below does — would manufacture an
        // exception out of a state the operator was entitled to submit
        // deliberately. Say the file didn't attach, name where to add it, and
        // leave the receipt alone.
        if (!isSlip) {
          const msg = uploadErr instanceof Error ? uploadErr.message : 'unknown error'
          alert(
            `The receipt was recorded, but the attachment failed to upload (${msg}). ` +
            'The receipt itself is fine — open it from the Agreement Receipts list (/receipts) ' +
            'to attach the file.'
          )
          // `finally` below still runs on this return — it owns the
          // isUploadingAttachment reset.
          resetForm()
          onSuccess?.()
          return
        }
        // The receipt already exists as `status: "open"` with no evidence on
        // it — create()'s open/pending_ap_review routing decision was made
        // from the request body BEFORE this upload ever ran (it can't see
        // the future), so a failed upload here does not, by itself, put the
        // receipt anywhere near AP review. Left alone, that's a receipt that
        // claims to need no photo, has none, and can still be claimed by an
        // invoice and paid — silently skipping the entire reason this
        // feature exists. Compensate: PATCH a reason onto it, which
        // crud.agreement_receipt.update() now routes to pending_ap_review the
        // same way create() would have if the reason had been there from
        // the start.
        const uploadErrMessage = uploadErr instanceof Error ? uploadErr.message : 'unknown error'
        // Fall back to date + total, NOT receipt.id: the table renders neither the
        // UUID nor any way to search by it, so naming the id would promise a
        // handle the user cannot actually use. Date and Total are always-visible
        // columns, and receipt_ref is optional (OCR may not find one and the user
        // may not type one), so this fallback is a routine path, not an edge case.
        const receiptLabel = receipt.receipt_ref
          ? `receipt ${receipt.receipt_ref}`
          : receipt.total_amount === null
            ? `the receipt dated ${receipt.receipt_date}`
            : `the receipt dated ${receipt.receipt_date} for ${receipt.total_amount}`
        try {
          await agreementReceiptService.update(agreementId, receipt.id, {
            missing_receipt_reason:
              `Photo upload failed after this receipt was recorded (${uploadErrMessage}). ` +
              'Needs manual follow-up: attach the photo or confirm no photo exists.',
          })
          // invalidateReceiptViews, not a hand-rolled invalidate of one key:
          // this PATCH moves the receipt to pending_ap_review, and AP reads
          // that queue on ReceiptListPage (['agreement-receipts']), which a
          // lone ['agreements', id, 'receipts'] invalidate leaves stale — the
          // very asymmetry that helper was created to make impossible. It also
          // covers ReceiptDetailPage's own key (Task 12).
          await invalidateReceiptViews(queryClient, agreementId)
          alert(
            `${receiptLabel} was recorded, but the photo failed to upload. ` +
            'It has been sent to AP review so it is not paid without evidence.'
          )
        } catch (patchErr) {
          // Do not swallow this. The compensating PATCH is the only thing
          // standing between "photo failed to upload" and "unreviewed receipt
          // silently sitting in open" — if IT also fails, the user is the
          // last line of defense and needs the exact receipt identified so
          // they can go fix it by hand.
          const patchErrMessage = patchErr instanceof Error ? patchErr.message : 'unknown error'
          alert(
            `${receiptLabel} was recorded, but the photo failed to upload AND the automatic ` +
            `follow-up to send it to AP review also failed (${patchErrMessage}). ` +
            // Whole-branch review (I3) told the user to Void and re-key,
            // because at the time that was the ONLY action the app could
            // actually perform on a saved receipt — no UI called PATCH or the
            // attachment routes. Task 12's detail page calls both, so the
            // honest instruction is now the cheap one: open the receipt and
            // attach the photo. Kept exact about WHERE, since this form is
            // also used standalone on ReceiptCreatePage (Task 10), which has
            // no receipt table on it at all — the Agreement Receipts list is
            // reachable regardless of which page recorded this.
            'The receipt is now sitting as "open" with no photo and no reason on it — which means it ' +
            'can be claimed by an invoice and paid as if it had evidence. Open the Agreement Receipts ' +
            'list (/receipts), click this receipt to open it, and either attach the photo or write in ' +
            'why there is none. If it was a mistake, Remove it there instead.'
          )
        }
      } finally {
        setIsUploadingAttachment(false)
      }
    }

    resetForm()
    onSuccess?.()
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      {/* Counter slip: the photo IS the record — scanned, read, and required
          unless explained. Delivery note / service sign-off: one optional
          attachment, no OCR, no consequence for leaving it empty. */}
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium text-neutral-700">
          {isSlip ? 'Receipt photo' : 'Attachment'}
          {!isSlip && <span className="ml-1.5 font-normal text-neutral-400">(optional)</span>}
        </label>
        {!file ? (
          <label
            htmlFor="receipt-photo-upload"
            className={cn(
              'flex cursor-pointer items-center gap-2 rounded-lg border-2 border-dashed border-neutral-300 text-center transition-colors hover:border-primary-400 hover:bg-primary-50',
              isSlip ? 'flex-col p-5' : 'justify-center px-4 py-3',
            )}
          >
            <Upload className={cn('text-neutral-400', isSlip ? 'h-6 w-6' : 'h-4 w-4')} />
            {isSlip ? (
              <>
                <p className="text-sm font-medium text-neutral-700">Take or upload a photo of the receipt</p>
                <p className="text-xs text-neutral-400">Vendor, amounts and date will be auto-filled — optional, but recommended</p>
              </>
            ) : (
              <p className="text-sm text-neutral-600">
                Attach the signed {receiptType === 'delivery' ? 'delivery note' : 'sign-off'} if you have one
              </p>
            )}
            <input id="receipt-photo-upload" type="file" accept="image/*,.pdf" className="sr-only" onChange={handleFileChange} />
          </label>
        ) : (
          <div className="flex items-center gap-2 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm">
            <span className="flex-1 truncate text-neutral-700">📎 {file.name}</span>
            <button type="button" onClick={clearFile} className="text-neutral-400 hover:text-danger-600" aria-label="Remove attachment">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {/* OCR only ever runs for a counter slip, so these three states are
            unreachable on the other types — no need to guard them separately. */}
        {ocrState === 'loading' && (
          <p className="flex items-center gap-1.5 text-xs text-neutral-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Reading receipt…
          </p>
        )}
        {ocrState === 'success' && (
          <p className="flex items-center gap-1.5 text-xs text-primary-700">
            <Info className="h-3.5 w-3.5 shrink-0" /> Auto-filled from the photo — please verify before submitting.
          </p>
        )}
        {ocrState === 'failed' && (
          <p className="flex items-center gap-1.5 text-xs text-neutral-500">
            <Info className="h-3.5 w-3.5 shrink-0" /> Couldn't auto-read this photo — enter the details manually.
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FormField label={copy.dateLabel} required htmlFor="receipt-date">
          <Input id="receipt-date" type="date" value={receiptDate} onChange={(e) => setReceiptDate(e.target.value)} required />
        </FormField>
        <FormField label={copy.refLabel} htmlFor="receipt-ref">
          {/* DB column is String(64) — a paste that overflows it raises a
              psycopg StringDataRightTruncation (DataError), not an
              IntegrityError, so create_receipt's `except IntegrityError` for the
              friendly 409 doesn't catch it and it falls through to a bare 500.
              maxLength stops the overflow from ever reaching the request. */}
          <Input id="receipt-ref" value={receiptRef} onChange={(e) => setReceiptRef(e.target.value)} placeholder={copy.refPlaceholder} maxLength={64} />
        </FormField>
        {isSlip && (
        <FormField
          label="Vendor on receipt"
          htmlFor="receipt-vendor"
          hint="The merchant printed on the slip. Pick it from the vendor list if it's there — otherwise just type what the slip says, or leave it blank if it isn't legible."
        >
          {/* Deliberately NOT required and never a blocker: an unmatched
              merchant leaves vendor_id null and saves the text, and a blank
              vendor is never reported as a mismatch at all. */}
          <ReceiptVendorPicker
            key={vendorPickerNonce}
            inputId="receipt-vendor"
            value={vendor}
            onChange={setVendor}
            suggestion={vendorSuggestion}
            disabled={isPending}
          />
        </FormField>
        )}
        {isSlip && (
          <>
            <FormField label="Amount (before tax)" required htmlFor="receipt-amount">
              <Input id="receipt-amount" type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
            </FormField>
            <FormField label="Tax" required htmlFor="receipt-tax">
              <Input id="receipt-tax" type="number" step="0.01" value={taxAmount} onChange={(e) => setTaxAmount(e.target.value)} />
            </FormField>
            <FormField label="Total" required error={amountError ?? undefined} htmlFor="receipt-total">
              <Input id="receipt-total" type="number" step="0.01" value={totalAmount} onChange={(e) => setTotalAmount(e.target.value)} />
            </FormField>
          </>
        )}
        <FormField label={copy.byLabel} required error={receivedByError ?? undefined} htmlFor="receipt-picked-by"
          hint={copy.byHint}>
          <select
            id="receipt-picked-by"
            value={receivedBy}
            onChange={(e) => setReceivedBy(e.target.value)}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            <option value="">Select…</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.full_name}</option>
            ))}
          </select>
        </FormField>
      </div>

      {isSlip && (
      <FormField
        label="Reason for missing photo"
        required={!file}
        error={reasonError ?? undefined}
        htmlFor="receipt-missing-reason"
        hint="No photo on file will route this receipt to AP review instead of posting it directly."
      >
        <textarea
          id="receipt-missing-reason"
          rows={2}
          value={missingReceiptReason}
          onChange={(e) => setMissingReceiptReason(e.target.value)}
          placeholder="e.g. receipt was lost / illegible / not issued"
          className="w-full resize-none rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
        />
      </FormField>
      )}

      <FormField label="Notes" htmlFor="receipt-notes">
        <textarea
          id="receipt-notes"
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Optional notes"
          className="w-full resize-none rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
        />
      </FormField>

      <div className="flex justify-end">
        <Button type="submit" size="sm" disabled={isPending} className={cn(isPending && 'cursor-not-allowed')}>
          {isPending ? 'Recording…' : 'Record Receipt'}
        </Button>
      </div>
    </form>
  )
}
