import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { Loader2, Plus, Trash2, AlertTriangle, CheckCircle2, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn, formatAmount, formatBytes } from '@/lib/utils'
import { useCreateFreightInvoice, useUploadFreightAttachment } from '@/hooks/useFreight'
import {
  freightService, readFreightInvoice,
  type ChargeType, type FreightCarrier, type FreightOcrResult,
} from '@/services/freight'
import {
  CHARGE_TYPE_LABEL, INPUT_SM_CLS, Modal, SELECT_SM_CLS, TH_CLS,
  addDaysISO, cents, errorMessage, isDateOnly, num,
} from '@/pages/freight/freightUi'

interface LineDraft {
  key: number
  charge_type: ChargeType
  tracking_no: string
  packages: string        // comma-separated package numbers
  reference: string
  ship_date: string       // YYYY-MM-DD or ''
  description: string
  amount: string
  tax_amount: string
}

let nextKey = 1
const emptyLine = (): LineDraft => ({
  key: nextKey++, charge_type: 'shipment', tracking_no: '', packages: '', reference: '',
  ship_date: '', description: '', amount: '', tax_amount: '0',
})

const splitPackages = (s: string) => s.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean)

type Step = 'reading' | 'review'

/**
 * The courier half of Invoices → Upload Invoice. The upload form hands over
 * once the invoice's vendor turns out to be a carrier's (Freight → Carriers):
 * a carrier's invoice is always a courier invoice. The same PDF is read again
 * for its per-shipment detail, and the reviewer checks the lines here.
 */
export function CourierInvoiceReview({ carrier, file, queueLabel, onBack, onClose, onCreated }: {
  carrier: FreightCarrier
  /** The PDF the upload form was given; null = entered by hand. */
  file: File | null
  /** "Invoice 2 of 5" when stepping through a split PDF. */
  queueLabel?: string
  /** Back to the upload form to pick another vendor (not a courier invoice after all). */
  onBack: () => void
  onClose: () => void
  onCreated: (id: string) => void
}) {
  const carrierId = carrier.id
  const [step, setStep] = useState<Step>(file ? 'reading' : 'review')
  const [ocrError, setOcrError] = useState<{ msg: string; kind: 'file' | 'service' } | null>(null)
  const [ocr, setOcr] = useState<FreightOcrResult | null>(null)

  // Review form
  const [invoiceNo, setInvoiceNo] = useState('')
  const [invoiceDate, setInvoiceDate] = useState('')
  const [dueDate, setDueDate] = useState('')
  const [dueTouched, setDueTouched] = useState(false)
  const [currency, setCurrency] = useState('CAD')
  const [amount, setAmount] = useState('')
  const [tax, setTax] = useState('0')
  const [notes, setNotes] = useState('')
  const [lines, setLines] = useState<LineDraft[]>([emptyLine()])
  const [saveError, setSaveError] = useState<string | null>(null)

  const createInvoice = useCreateFreightInvoice()
  const uploadAttachment = useUploadFreightAttachment()
  const saving = createInvoice.isPending || uploadAttachment.isPending

  // ── Duplicate check (carrier + invoice number) ────────────────────────────
  const [dup, setDup] = useState<{ checking: boolean; duplicate: boolean; detail: string | null }>({
    checking: false, duplicate: false, detail: null,
  })
  useEffect(() => {
    const n = invoiceNo.trim()
    if (step !== 'review' || !carrierId || !n) {
      setDup({ checking: false, duplicate: false, detail: null })
      return
    }
    let live = true
    setDup((d) => ({ ...d, checking: true }))
    const t = setTimeout(() => {
      freightService.duplicateCheck(carrierId, n)
        .then((r) => { if (live) setDup({ checking: false, duplicate: r.duplicate, detail: r.detail }) })
        .catch(() => { if (live) setDup({ checking: false, duplicate: false, detail: null }) })
    }, 400)
    return () => { live = false; clearTimeout(t) }
  }, [carrierId, invoiceNo, step])

  // ── Read the PDF ──────────────────────────────────────────────────────────
  const fillFrom = (r: FreightOcrResult) => {
    setInvoiceNo(r.invoice_number ?? '')
    const inv = isDateOnly(r.invoice_date) ? r.invoice_date : ''
    setInvoiceDate(inv)
    setDueDate(isDateOnly(r.due_date) ? r.due_date : (inv ? addDaysISO(inv, 30) : ''))
    setDueTouched(isDateOnly(r.due_date))
    setCurrency(r.currency || 'CAD')
    setAmount(r.amount ?? '')
    setTax(r.tax_amount ?? '0')
    setLines(r.lines.length > 0
      ? r.lines.map((l) => ({
          key: nextKey++,
          charge_type: l.charge_type,
          tracking_no: l.tracking_no ?? '',
          // The lead number is stored with the packages server-side anyway;
          // listing only the children here keeps the field readable.
          packages: l.package_tracking_nos.filter((p) => p !== l.tracking_no).join(', '),
          reference: l.reference ?? '',
          ship_date: isDateOnly(l.ship_date) ? l.ship_date : '',
          description: l.description ?? '',
          amount: l.amount,
          tax_amount: l.tax_amount,
        }))
      : [emptyLine()])
  }

  // Read once, on arrival. `readStarted` keeps React's dev double-mount from
  // paying for a second model call; `mounted` (not a per-run flag) decides
  // whether the answer still has somewhere to go — a per-run flag would be
  // cleared by that double-mount and the only read's answer thrown away,
  // leaving the form "reading" for ever.
  const readStarted = useRef(false)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    if (!file || readStarted.current) return
    readStarted.current = true
    readFreightInvoice(file).then((res) => {
      if (!mounted.current) return
      if (!res.ok) {
        setOcrError({
          kind: res.kind,
          msg: res.kind === 'service'
            ? 'The AI reading service is unavailable right now. This is not a problem with your file — enter the shipment lines by hand, and let IT know.'
            : `The shipment detail could not be read: ${res.error}. Enter the lines by hand.`,
        })
      } else {
        setOcr(res.data)
        fillFrom(res.data)
      }
      setStep('review')
    })
  }, [file]) // eslint-disable-line react-hooks/exhaustive-deps

  const changeInvoiceDate = (v: string) => {
    setInvoiceDate(v)
    if (!dueTouched && isDateOnly(v)) setDueDate(addDaysISO(v, 30))
  }

  const updateLine = (key: number, patch: Partial<LineDraft>) =>
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)))

  // ── Live checks ───────────────────────────────────────────────────────────
  const linesAmount = cents(lines.reduce((s, l) => s + num(l.amount), 0))
  const linesTax = cents(lines.reduce((s, l) => s + num(l.tax_amount), 0))
  const headerAmount = cents(num(amount))
  const headerTax = cents(num(tax))
  const amountOk = amount.trim() !== '' && linesAmount === headerAmount
  const taxOk = linesTax === headerTax
  const ocrCarrierMismatch = !!ocr?.carrier && ocr.carrier !== 'OTHER' && !!carrier &&
    carrier.code.toUpperCase() !== ocr.carrier.toUpperCase()

  const save = async () => {
    if (saving) return
    setSaveError(null)
    if (!invoiceNo.trim()) return setSaveError('Enter the invoice number.')
    if (!isDateOnly(invoiceDate)) return setSaveError('Enter the invoice date.')
    if (!isDateOnly(dueDate)) return setSaveError('Enter the due date.')
    if (amount.trim() === '' || isNaN(Number(amount))) return setSaveError('Enter the amount before tax.')
    if (isNaN(Number(tax))) return setSaveError('The tax amount is not a number.')
    if (lines.length === 0) return setSaveError('Add at least one line.')
    const bad = lines.findIndex((l) => l.amount.trim() === '' || isNaN(Number(l.amount)) || isNaN(Number(l.tax_amount || 0)))
    if (bad >= 0) return setSaveError(`Line ${bad + 1} needs a valid amount.`)
    if (dup.checking) return setSaveError('Still checking for a duplicate invoice — try again in a moment.')
    if (dup.duplicate) return setSaveError(`This invoice is already recorded${dup.detail ? ` (${dup.detail})` : ''}.`)

    let created
    try {
      created = await createInvoice.mutateAsync({
        carrier_id: carrierId,
        vendor_invoice_number: invoiceNo.trim(),
        invoice_date: invoiceDate,
        due_date: dueDate,
        currency: currency.trim().toUpperCase() || 'CAD',
        amount: Number(amount),
        tax_amount: Number(tax || 0),
        file_name: file?.name ?? null,
        file_size: file ? formatBytes(file.size) : null,
        notes: notes.trim() || null,
        lines: lines.map((l) => ({
          charge_type: l.charge_type,
          tracking_no: l.tracking_no.trim() || null,
          package_tracking_nos: splitPackages(l.packages),
          reference: l.reference.trim() || null,
          ship_date: l.ship_date || null,
          description: l.description.trim() || null,
          amount: Number(l.amount),
          tax_amount: Number(l.tax_amount || 0),
        })),
      })
    } catch (e) {
      setSaveError(errorMessage(e, 'Could not save the invoice'))
      return
    }
    if (file) {
      try {
        await uploadAttachment.mutateAsync({ invoiceId: created.id, file })
      } catch (e) {
        // The invoice exists; only the PDF is missing. Say so and carry on —
        // the invoice page can attach it again.
        alert(`The invoice was saved, but the PDF did not attach: ${errorMessage(e)}. Attach it again from the invoice page.`)
      }
    }
    onCreated(created.id)
  }

  // ── Render ────────────────────────────────────────────────────────────────
  if (step === 'reading') {
    return (
      <Modal title="Courier invoice" subtitle={`${carrier.name}${file ? ` · ${file.name}` : ''}`} onClose={onClose}>
        <div className="flex items-center gap-2 rounded-lg border border-primary-100 bg-primary-50 px-3 py-2.5 text-sm text-primary-700">
          <Loader2 className="h-4 w-4 animate-spin" />
          {carrier.vendor_name ?? carrier.name} is a courier — reading the invoice shipment by shipment. A long bill can take up to a minute…
        </div>
      </Modal>
    )
  }

  return (
    <Modal wide title={queueLabel ? `Check the Courier Invoice — ${queueLabel}` : 'Check the Courier Invoice'}
      subtitle={`${carrier.name}${file ? ` · ${file.name}` : ' · entered by hand'}`} onClose={onClose}>
      <div className="flex flex-col gap-5">
        <div className="flex items-start justify-between gap-3 rounded-lg border border-primary-100 bg-primary-50 px-3 py-2.5 text-xs text-primary-800">
          <p>
            <span className="font-semibold">{carrier.vendor_name ?? carrier.name}</span> is a courier (Freight → Carriers), so this is a
            courier invoice: check one line per shipment, pickup or fee below. After saving, each line is matched to a
            registered tracking number and cost center.
          </p>
          <button type="button" onClick={onBack} disabled={saving}
            className="inline-flex shrink-0 items-center gap-1 font-medium text-primary-700 hover:underline disabled:opacity-40">
            <ArrowLeft className="h-3.5 w-3.5" /> Different vendor
          </button>
        </div>
        {ocrError && (
          <div className={cn('rounded-lg border px-3 py-2.5 text-xs',
            ocrError.kind === 'service' ? 'border-warning-200 bg-warning-50 text-warning-700' : 'border-danger-200 bg-danger-50 text-danger-600')}>
            {ocrError.msg}
          </div>
        )}
        {(ocr?.warnings.length ?? 0) > 0 && (
          <div className="rounded-lg border border-warning-200 bg-warning-50 px-3 py-2.5 text-xs text-warning-700">
            <p className="font-semibold mb-1 flex items-center gap-1.5"><AlertTriangle className="h-3.5 w-3.5" /> Check these before saving</p>
            <ul className="list-disc pl-5 space-y-0.5">
              {ocr!.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        )}
        {ocrCarrierMismatch && (
          <div className="rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700">
            This reads like a {ocr!.carrier} invoice, but the vendor is {carrier.vendor_name ?? carrier.name}'s ({carrier.name}). Check the vendor.
          </div>
        )}

        {/* Header */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Carrier</label>
            <div className="flex h-10 items-center rounded-lg border border-neutral-200 bg-neutral-50 px-3 text-sm font-medium text-neutral-800">
              {carrier.name}
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Invoice number *</label>
            <Input value={invoiceNo} onChange={(e: ChangeEvent<HTMLInputElement>) => setInvoiceNo(e.target.value)}
              className="font-mono" error={dup.duplicate} />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Invoice date *</label>
            <Input type="date" value={invoiceDate} onChange={(e: ChangeEvent<HTMLInputElement>) => changeInvoiceDate(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Due date *</label>
            <Input type="date" value={dueDate} onChange={(e: ChangeEvent<HTMLInputElement>) => { setDueDate(e.target.value); setDueTouched(true) }} />
            {!dueTouched && dueDate && <p className="text-[10px] text-neutral-400">Not printed — invoice date + 30 days</p>}
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Currency</label>
            <Input value={currency} onChange={(e: ChangeEvent<HTMLInputElement>) => setCurrency(e.target.value)} maxLength={3} className="uppercase" />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Amount before tax *</label>
            <Input type="number" step="0.01" value={amount} onChange={(e: ChangeEvent<HTMLInputElement>) => setAmount(e.target.value)} className="font-mono" />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Tax</label>
            <Input type="number" step="0.01" value={tax} onChange={(e: ChangeEvent<HTMLInputElement>) => setTax(e.target.value)} className="font-mono" />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Total</label>
            <div className="flex h-10 items-center rounded-lg border border-neutral-200 bg-neutral-50 px-3 font-mono text-sm font-semibold text-neutral-800">
              {formatAmount(cents(headerAmount + headerTax), currency || 'CAD')}
            </div>
          </div>
        </div>
        {dup.duplicate && (
          <p className="-mt-3 text-xs font-medium text-danger-600">
            Invoice {invoiceNo.trim()} from {carrier.name} is already recorded{dup.detail ? ` (${dup.detail})` : ''}. It cannot be saved twice.
          </p>
        )}

        {/* Checks */}
        <div className="flex flex-wrap gap-3 text-xs">
          <CheckChip ok={amountOk}
            okText={`Lines add up to the amount (${formatAmount(linesAmount, currency || 'CAD')})`}
            badText={`Lines add up to ${formatAmount(linesAmount, currency || 'CAD')} before tax; the invoice says ${formatAmount(headerAmount, currency || 'CAD')} (difference ${formatAmount(cents(headerAmount - linesAmount), currency || 'CAD')}). The invoice cannot be finalised until they agree.`} />
          <CheckChip ok={taxOk} soft
            okText={`Line taxes add up to the tax (${formatAmount(linesTax, currency || 'CAD')})`}
            badText={`Line taxes add up to ${formatAmount(linesTax, currency || 'CAD')}; the invoice says ${formatAmount(headerTax, currency || 'CAD')}. The difference is spread over the lines by amount when you save.`} />
        </div>

        {/* Lines */}
        <div className="rounded-xl border border-neutral-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-100 bg-neutral-50">
                  {['#', 'Type', 'Tracking No', 'Other packages', 'Reference', 'Date', 'Description', 'Amount', 'Tax', ''].map((h) => (
                    <th key={h} className={cn(TH_CLS, ['Amount', 'Tax'].includes(h) && 'text-right')}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {lines.map((l, i) => (
                  <tr key={l.key} className="border-b border-neutral-100 last:border-0 align-top">
                    <td className="px-3 py-2 text-xs text-neutral-400">{i + 1}</td>
                    <td className="px-2 py-1.5">
                      <select value={l.charge_type} onChange={(e) => updateLine(l.key, { charge_type: e.target.value as ChargeType })} className={SELECT_SM_CLS}>
                        {Object.entries(CHARGE_TYPE_LABEL).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                      </select>
                    </td>
                    <td className="px-2 py-1.5"><input value={l.tracking_no} onChange={(e) => updateLine(l.key, { tracking_no: e.target.value })} className={cn(INPUT_SM_CLS, 'w-40 font-mono')} /></td>
                    <td className="px-2 py-1.5"><input value={l.packages} onChange={(e) => updateLine(l.key, { packages: e.target.value })} placeholder="comma separated" className={cn(INPUT_SM_CLS, 'w-40 font-mono')} /></td>
                    <td className="px-2 py-1.5"><input value={l.reference} onChange={(e) => updateLine(l.key, { reference: e.target.value })} className={cn(INPUT_SM_CLS, 'w-28')} /></td>
                    <td className="px-2 py-1.5"><input type="date" value={l.ship_date} onChange={(e) => updateLine(l.key, { ship_date: e.target.value })} className={cn(INPUT_SM_CLS, 'w-32')} /></td>
                    <td className="px-2 py-1.5"><input value={l.description} onChange={(e) => updateLine(l.key, { description: e.target.value })} className={cn(INPUT_SM_CLS, 'w-56')} /></td>
                    <td className="px-2 py-1.5"><input type="number" step="0.01" value={l.amount} onChange={(e) => updateLine(l.key, { amount: e.target.value })} className={cn(INPUT_SM_CLS, 'w-24 text-right font-mono')} /></td>
                    <td className="px-2 py-1.5"><input type="number" step="0.01" value={l.tax_amount} onChange={(e) => updateLine(l.key, { tax_amount: e.target.value })} className={cn(INPUT_SM_CLS, 'w-20 text-right font-mono')} /></td>
                    <td className="px-2 py-1.5">
                      <button type="button" onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}
                        disabled={lines.length === 1}
                        className="rounded-md p-1.5 text-neutral-400 hover:bg-danger-50 hover:text-danger-600 disabled:opacity-30" aria-label="Remove line">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-neutral-200 bg-neutral-50 text-xs font-semibold">
                  <td colSpan={7} className="px-3 py-2">
                    <button type="button" onClick={() => setLines((ls) => [...ls, emptyLine()])}
                      className="inline-flex items-center gap-1 text-primary-700 hover:underline">
                      <Plus className="h-3.5 w-3.5" /> Add line
                    </button>
                  </td>
                  <td className={cn('px-3 py-2 text-right font-mono', amountOk ? 'text-neutral-800' : 'text-danger-600')}>{formatAmount(linesAmount, currency || 'CAD')}</td>
                  <td className={cn('px-3 py-2 text-right font-mono', taxOk ? 'text-neutral-800' : 'text-warning-600')}>{formatAmount(linesTax, currency || 'CAD')}</td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Notes</label>
          <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)}
            className="w-full resize-none rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
        </div>

        {saveError && <p className="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-600">{saveError}</p>}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={save} disabled={saving || dup.duplicate || dup.checking}>
            {createInvoice.isPending ? 'Saving…' : uploadAttachment.isPending ? 'Attaching PDF…' : 'Save and match'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

function CheckChip({ ok, okText, badText, soft = false }: { ok: boolean; okText: string; badText: string; soft?: boolean }) {
  return (
    <div className={cn('flex items-start gap-1.5 rounded-lg border px-3 py-2',
      ok ? 'border-success-200 bg-success-50 text-success-700'
        : soft ? 'border-warning-200 bg-warning-50 text-warning-700' : 'border-danger-200 bg-danger-50 text-danger-600')}>
      {ok ? <CheckCircle2 className="h-3.5 w-3.5 mt-px shrink-0" /> : <AlertTriangle className="h-3.5 w-3.5 mt-px shrink-0" />}
      <span>{ok ? okText : badText}</span>
    </div>
  )
}
