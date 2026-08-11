import { useState } from 'react'
import { Upload, Loader2, Info, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { cn, todayISODate } from '@/lib/utils'
import { useUserDirectory } from '@/hooks/useUsers'
import { useCreateSlip } from '@/hooks/useAgreementSlips'
import { agreementSlipAttachmentService } from '@/services/agreementSlipAttachments'
import { ocrService } from '@/services/agreementSlips'

// The backend (schemas/agreement_slip.py::validate_totals) checks
// amount + tax_amount == total_amount as exact Decimal equality against a
// Numeric(15,2) column — i.e. equality to the cent. A plain float `===`
// (or a 0.01 tolerance, which is NOT tight enough: 10.00 + 1.30 vs 11.31
// differs by 0.009999999999999787, which slips under a 0.01 threshold and
// then 422s server-side) fails to match that. Comparing rounded-to-cent
// integers kills float noise (0.1 + 0.2 !== 0.3) while staying exactly as
// strict as the backend, so a value the frontend accepts never bounces off
// the API.
function centsEqual(total: number, amount: number, tax: number): boolean {
  return Math.round(total * 100) === Math.round(amount * 100) + Math.round(tax * 100)
}

interface SlipEntryFormProps {
  agreementId: string
}

// Entry flow: pick a photo → auto OCR → prefill (still fully editable) →
// fill in picked_by → submit the row → upload the photo as an attachment
// against the slip id just returned. Two separate API calls, same pattern as
// PrCreatePage (create doc, then upload attachments against the new id) —
// there is no single create-with-attachment endpoint.
export function SlipEntryForm({ agreementId }: SlipEntryFormProps) {
  const { data: usersData } = useUserDirectory()
  const users = usersData?.items ?? []
  const createSlip = useCreateSlip(agreementId)

  const [file, setFile] = useState<File | null>(null)
  const [ocrState, setOcrState] = useState<'idle' | 'loading' | 'success' | 'failed'>('idle')

  const [slipDate, setSlipDate] = useState(todayISODate())
  const [slipRef, setSlipRef] = useState('')
  const [amount, setAmount] = useState('')
  const [taxAmount, setTaxAmount] = useState('')
  const [totalAmount, setTotalAmount] = useState('')
  const [pickedBy, setPickedBy] = useState('')
  const [missingSlipReason, setMissingSlipReason] = useState('')
  const [notes, setNotes] = useState('')

  const [amountError, setAmountError] = useState<string | null>(null)
  const [pickedByError, setPickedByError] = useState<string | null>(null)
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [isUploadingAttachment, setIsUploadingAttachment] = useState(false)

  const resetForm = () => {
    setFile(null)
    setOcrState('idle')
    setSlipDate(todayISODate())
    setSlipRef('')
    setAmount('')
    setTaxAmount('')
    setTotalAmount('')
    setPickedBy('')
    setMissingSlipReason('')
    setNotes('')
    setAmountError(null)
    setPickedByError(null)
    setReasonError(null)
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0]
    e.target.value = ''
    if (!picked) return
    setFile(picked)
    setOcrState('loading')
    try {
      const fields = await ocrService.slip(picked)
      setOcrState('success')
      if (fields.slip_ref) setSlipRef(fields.slip_ref)
      if (fields.date) setSlipDate(fields.date)
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

  const isPending = createSlip.isPending || isUploadingAttachment

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    let hasError = false
    if (!pickedBy) {
      setPickedByError('Required')
      hasError = true
    } else {
      setPickedByError(null)
    }
    if (!file && !missingSlipReason.trim()) {
      setReasonError('Required when no photo is attached')
      hasError = true
    } else {
      setReasonError(null)
    }
    const amt = Number(amount)
    const tax = Number(taxAmount)
    const tot = Number(totalAmount)
    if (amount === '' || taxAmount === '' || totalAmount === '' || Number.isNaN(amt) || Number.isNaN(tax) || Number.isNaN(tot)) {
      setAmountError('Amount, tax and total are all required')
      hasError = true
    } else if (!centsEqual(tot, amt, tax)) {
      setAmountError('Total must equal amount + tax')
      hasError = true
    } else {
      setAmountError(null)
    }
    if (!slipDate) hasError = true
    if (hasError) return

    const slip = await createSlip.mutateAsync({
      slip_date: slipDate,
      slip_ref: slipRef.trim() || undefined,
      amount: amt,
      tax_amount: tax,
      total_amount: tot,
      picked_by: pickedBy,
      missing_slip_reason: !file ? missingSlipReason.trim() || undefined : undefined,
      notes: notes.trim() || undefined,
    })

    if (file) {
      setIsUploadingAttachment(true)
      try {
        await agreementSlipAttachmentService.upload(agreementId, slip.id, file)
      } catch (err) {
        alert(err instanceof Error ? err.message : 'Slip was recorded, but the photo failed to upload.')
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
        <label className="text-sm font-medium text-neutral-700">Slip photo</label>
        {!file ? (
          <label
            htmlFor="slip-photo-upload"
            className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed border-neutral-300 p-5 text-center hover:border-primary-400 hover:bg-primary-50 transition-colors"
          >
            <Upload className="h-6 w-6 text-neutral-400" />
            <p className="text-sm font-medium text-neutral-700">Take or upload a photo of the slip</p>
            <p className="text-xs text-neutral-400">Amounts and date will be auto-filled — optional, but recommended</p>
            <input id="slip-photo-upload" type="file" accept="image/*,.pdf" className="sr-only" onChange={handleFileChange} />
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
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Reading slip…
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
        <FormField label="Slip date" required htmlFor="slip-date">
          <Input id="slip-date" type="date" value={slipDate} onChange={(e) => setSlipDate(e.target.value)} required />
        </FormField>
        <FormField label="Reference #" htmlFor="slip-ref">
          {/* DB column is String(64) — a paste that overflows it raises a
              psycopg StringDataRightTruncation (DataError), not an
              IntegrityError, so create_slip's `except IntegrityError` for the
              friendly 409 doesn't catch it and it falls through to a bare 500.
              maxLength stops the overflow from ever reaching the request. */}
          <Input id="slip-ref" value={slipRef} onChange={(e) => setSlipRef(e.target.value)} placeholder="Slip / receipt number" maxLength={64} />
        </FormField>
        <FormField label="Amount (before tax)" required htmlFor="slip-amount">
          <Input id="slip-amount" type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </FormField>
        <FormField label="Tax" required htmlFor="slip-tax">
          <Input id="slip-tax" type="number" step="0.01" value={taxAmount} onChange={(e) => setTaxAmount(e.target.value)} />
        </FormField>
        <FormField label="Total" required error={amountError ?? undefined} htmlFor="slip-total">
          <Input id="slip-total" type="number" step="0.01" value={totalAmount} onChange={(e) => setTotalAmount(e.target.value)} />
        </FormField>
        <FormField label="Picked up by" required error={pickedByError ?? undefined} htmlFor="slip-picked-by"
          hint="The person who brought the slip in — not a sign-off or approval.">
          <select
            id="slip-picked-by"
            value={pickedBy}
            onChange={(e) => setPickedBy(e.target.value)}
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
        htmlFor="slip-missing-reason"
        hint="No photo on file will route this slip to AP review instead of posting it directly."
      >
        <textarea
          id="slip-missing-reason"
          rows={2}
          value={missingSlipReason}
          onChange={(e) => setMissingSlipReason(e.target.value)}
          placeholder="e.g. slip was lost / illegible / not issued"
          className="w-full resize-none rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
        />
      </FormField>

      <FormField label="Notes" htmlFor="slip-notes">
        <textarea
          id="slip-notes"
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Optional notes"
          className="w-full resize-none rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
        />
      </FormField>

      <div className="flex justify-end">
        <Button type="submit" size="sm" disabled={isPending} className={cn(isPending && 'cursor-not-allowed')}>
          {isPending ? 'Recording…' : 'Record Slip'}
        </Button>
      </div>
    </form>
  )
}
