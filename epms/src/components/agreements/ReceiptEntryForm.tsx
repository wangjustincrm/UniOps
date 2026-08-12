import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Upload, Loader2, Info, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { cn, todayISODate } from '@/lib/utils'
import { useUserDirectory } from '@/hooks/useUsers'
import { useCreateReceipt } from '@/hooks/useAgreementReceipts'
import { agreementReceiptAttachmentService } from '@/services/agreementReceiptAttachments'
import { agreementReceiptService, ocrService, type ReceiptType } from '@/services/agreementReceipts'
import { receiptAttachmentsQueryKey } from './ReceiptTable'

// The backend (schemas/agreement_receipt.py::validate_totals) checks
// amount + tax_amount == total_amount as exact Decimal equality against a
// Numeric(15,2) column — i.e. equality to the cent. A plain float `===`
// (or a 0.01 tolerance, which is NOT tight enough: 10.00 + 1.30 vs 11.31
// differs by 0.009999999999999787, which receipts under a 0.01 threshold and
// then 422s server-side) fails to match that. Comparing rounded-to-cent
// integers kills float noise (0.1 + 0.2 !== 0.3) while staying exactly as
// strict as the backend, so a value the frontend accepts never bounces off
// the API.
function centsEqual(total: number, amount: number, tax: number): boolean {
  return Math.round(total * 100) === Math.round(amount * 100) + Math.round(tax * 100)
}

interface ReceiptEntryFormProps {
  agreementId: string
  // Chosen by the caller — ReceiptCreatePage's type selector (Task 10). Defaults
  // to 'counter_slip' to match the backend's own default (schemas/agreement_receipt.py
  // ReceiptCreate.receipt_type) for the sake of any future embedding that omits it.
  receiptType?: ReceiptType
}

// Entry flow: pick a photo → auto OCR → prefill (still fully editable) →
// fill in received_by → submit the row → upload the photo as an attachment
// against the receipt id just returned. Two separate API calls, same pattern as
// PrCreatePage (create doc, then upload attachments against the new id) —
// there is no single create-with-attachment endpoint.
export function ReceiptEntryForm({ agreementId, receiptType = 'counter_slip' }: ReceiptEntryFormProps) {
  const { data: usersData } = useUserDirectory()
  const users = usersData?.items ?? []
  const createReceipt = useCreateReceipt(agreementId)
  const queryClient = useQueryClient()

  const [file, setFile] = useState<File | null>(null)
  const [ocrState, setOcrState] = useState<'idle' | 'loading' | 'success' | 'failed'>('idle')

  const [receiptDate, setReceiptDate] = useState(todayISODate())
  const [receiptRef, setReceiptRef] = useState('')
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
    setOcrState('loading')
    try {
      const fields = await ocrService.receipt(picked)
      setOcrState('success')
      if (fields.receipt_ref) setReceiptRef(fields.receipt_ref)
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
    if (!file && !missingReceiptReason.trim()) {
      setReasonError('Required when no photo is attached')
      hasError = true
    } else {
      setReasonError(null)
    }
    const amt = Number(amount)
    const tax = Number(taxAmount)
    const tot = Number(totalAmount)
    if (amount === '' || taxAmount === '' || totalAmount === '' || Number.isNaN(amt) || Number.isNaN(tax) || Number.isNaN(tot)) {
      setAmountError('Amount, tax and total are all required — enter 0 for tax if the receipt shows none')
      hasError = true
    } else if (!centsEqual(tot, amt, tax)) {
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
      amount: amt,
      tax_amount: tax,
      total_amount: tot,
      received_by: receivedBy,
      missing_receipt_reason: !file ? missingReceiptReason.trim() || undefined : undefined,
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
      } catch (uploadErr) {
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
          : `the receipt dated ${receipt.receipt_date} for ${receipt.total_amount}`
        try {
          await agreementReceiptService.update(agreementId, receipt.id, {
            missing_receipt_reason:
              `Photo upload failed after this receipt was recorded (${uploadErrMessage}). ` +
              'Needs manual follow-up: attach the photo or confirm no photo exists.',
          })
          await queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'receipts'] })
          alert(
            `${receiptLabel} was recorded, but the photo failed to upload. ` +
            'It has been sent to AP review so it is not paid without evidence.'
          )
        } catch (patchErr) {
          // Do not swallow this. The compensating PATCH is the only thing
          // standing between "photo failed to upload" and "unreviewed receipt
          // silently sitting in open" — if IT also fails, the user is the
          // last line of defense and needs the exact receipt identified so
          // they can go fix it by hand (edit/void it, or retry the upload).
          const patchErrMessage = patchErr instanceof Error ? patchErr.message : 'unknown error'
          alert(
            `${receiptLabel} was recorded, but the photo failed to upload AND the automatic ` +
            `follow-up to send it to AP review also failed (${patchErrMessage}). ` +
            'This receipt needs to be handled manually — find it in the table below and either ' +
            're-attach the photo, edit in a reason, or void it.'
          )
        }
      } finally {
        setIsUploadingAttachment(false)
      }
    }

    resetForm()
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      {/* Photo / OCR */}
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium text-neutral-700">Receipt photo</label>
        {!file ? (
          <label
            htmlFor="receipt-photo-upload"
            className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed border-neutral-300 p-5 text-center hover:border-primary-400 hover:bg-primary-50 transition-colors"
          >
            <Upload className="h-6 w-6 text-neutral-400" />
            <p className="text-sm font-medium text-neutral-700">Take or upload a photo of the receipt</p>
            <p className="text-xs text-neutral-400">Amounts and date will be auto-filled — optional, but recommended</p>
            <input id="receipt-photo-upload" type="file" accept="image/*,.pdf" className="sr-only" onChange={handleFileChange} />
          </label>
        ) : (
          <div className="flex items-center gap-2 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm">
            <span className="flex-1 truncate text-neutral-700">📎 {file.name}</span>
            <button type="button" onClick={clearFile} className="text-neutral-400 hover:text-danger-600" aria-label="Remove photo">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

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
        <FormField label="Receipt date" required htmlFor="receipt-date">
          <Input id="receipt-date" type="date" value={receiptDate} onChange={(e) => setReceiptDate(e.target.value)} required />
        </FormField>
        <FormField label="Reference #" htmlFor="receipt-ref">
          {/* DB column is String(64) — a paste that overflows it raises a
              psycopg StringDataRightTruncation (DataError), not an
              IntegrityError, so create_receipt's `except IntegrityError` for the
              friendly 409 doesn't catch it and it falls through to a bare 500.
              maxLength stops the overflow from ever reaching the request. */}
          <Input id="receipt-ref" value={receiptRef} onChange={(e) => setReceiptRef(e.target.value)} placeholder="Receipt number" maxLength={64} />
        </FormField>
        <FormField label="Amount (before tax)" required htmlFor="receipt-amount">
          <Input id="receipt-amount" type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </FormField>
        <FormField label="Tax" required htmlFor="receipt-tax">
          <Input id="receipt-tax" type="number" step="0.01" value={taxAmount} onChange={(e) => setTaxAmount(e.target.value)} />
        </FormField>
        <FormField label="Total" required error={amountError ?? undefined} htmlFor="receipt-total">
          <Input id="receipt-total" type="number" step="0.01" value={totalAmount} onChange={(e) => setTotalAmount(e.target.value)} />
        </FormField>
        <FormField label="Picked up by" required error={receivedByError ?? undefined} htmlFor="receipt-picked-by"
          hint="The person who brought the receipt in — not a sign-off or approval.">
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
