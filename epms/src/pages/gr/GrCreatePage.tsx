import { useState, useEffect, useRef } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { ArrowLeft, AlertTriangle, CheckCircle2, Package, Paperclip, X, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth.store'
import { usePo, usePos } from '@/hooks/usePos'
import { useCreateGr } from '@/hooks/useGrs'
import { type GrLineCondition, type GrAttachmentIn } from '@/services/gr'
import { type ApiPo } from '@/services/po'

const MAX_FILE_SIZE = 10 * 1024 * 1024  // 10 MB

async function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve((reader.result as string).split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

const TYPE_LABELS: Record<number, string> = {
  1: 'Raw Materials / Packaging',
  2: 'Consumables / Misc',
  3: 'Spare Parts',
  4: 'Service',
  5: 'Fixed Asset',
  6: 'Project-Related',
}

const CONDITION_OPTIONS: { value: GrLineCondition; label: string; color: string }[] = [
  { value: 'good',        label: 'Good',        color: 'text-success-600' },
  { value: 'discrepancy', label: 'Discrepancy', color: 'text-warning-600' },
  { value: 'damaged',     label: 'Damaged',     color: 'text-danger-600'  },
]

// type 4 = Service, type 6 = Project — non-physical GR flow; everything else is physical
const isPhysicalGr = (type: number) => type !== 4 && type !== 6

// Which POs can still take a goods receipt. Mirrors the server-side gate in
// `POST /gr` exactly (physical: issued/partially_received; service & project:
// also approved) — keep the two in step, or the picker offers POs the API
// rejects with a 409.
const canReceive = (p: ApiPo) =>
  p.status === 'issued' ||
  p.status === 'partially_received' ||
  ((p.type === 4 || p.type === 6) && p.status === 'approved')

interface FormGrLineItem {
  id: string
  po_line_id: string
  description: string
  material_id?: string
  qty_ordered: number
  qty_received: number
  unit: string
  unit_price: number
  line_total: number
  condition: GrLineCondition
  discrepancy_notes?: string
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function GrCreatePage() {
  const replaceTab = useReplaceTab(epmsRoutes)
  const [searchParams] = useSearchParams()
  const createGr = useCreateGr()
  const { data: posData } = usePos()
  const eligiblePos = (posData?.items ?? []).filter(canReceive)
  const { user } = useAuthStore()

  const preselectedPoId = searchParams.get('poId') ?? ''
  const [selectedPoId, setSelectedPoId] = useState(preselectedPoId)
  const [poSearch, setPoSearch] = useState('')
  const [showPoDropdown, setShowPoDropdown] = useState(false)

  // A deep link (?poId=… from the PO detail page, or from a confirm_receipt
  // reminder mail / task) fetches that one PO on its own instead of waiting for
  // the picker's list: the list pages through every PO in the system — 30+
  // parallel requests — and on this path the picker is never opened at all.
  // GET /po/{id} enforces the same visibility scope as the list, so this widens
  // nothing; it only makes the preselected PO arrive sooner and independently.
  const { data: deepLinkedPo } = usePo(preselectedPoId)

  const listedPo = eligiblePos.find((p) => p.id === selectedPoId) ?? null
  const linkedPo =
    deepLinkedPo && deepLinkedPo.id === selectedPoId && canReceive(deepLinkedPo)
      ? deepLinkedPo
      : null
  const selectedPo: ApiPo | null = listedPo ?? linkedPo

  // The deep link resolved to a real PO that can no longer take a receipt —
  // typically a reminder mail opened after somebody else already received the
  // goods. Say so, instead of dropping the user on a blank picker.
  const linkedPoNotReceivable =
    deepLinkedPo && deepLinkedPo.id === selectedPoId && !canReceive(deepLinkedPo)
      ? deepLinkedPo
      : null

  const [receivedAt, setReceivedAt] = useState(new Date().toISOString().slice(0, 10))
  const [storageLocation, setStorageLocation] = useState('')
  const [notes, setNotes] = useState('')
  const [lineItems, setLineItems] = useState<FormGrLineItem[]>([])
  const [packListFiles, setPackListFiles] = useState<File[]>([])
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [submitted, setSubmitted] = useState(false)

  // When PO changes, rebuild line items from PO lines.
  //
  // Keyed on a ref instead of on selectedPoId alone. With a deep link
  // (?poId=… from the PO detail page or a confirm_receipt reminder) the id is
  // already set on the very first render, while the PO query is still in
  // flight — so selectedPo is null. An effect that watched only the id ran
  // once against no data, cleared the lines, and never ran again once the PO
  // arrived: the summary card rendered but "Items Received" stayed at 0 lines
  // forever and the GR could not be saved. So: wait for the PO instead of
  // clearing, and let the ref keep the prefill to once per PO so a background
  // refetch cannot overwrite quantities the user has already typed.
  const appliedForPoRef = useRef<string | null>(null)
  useEffect(() => {
    if (!selectedPoId) { appliedForPoRef.current = null; setLineItems([]); return }
    if (!selectedPo) return
    if (appliedForPoRef.current === selectedPoId) return
    appliedForPoRef.current = selectedPoId
    setLineItems(
      selectedPo.line_items.map((li) => ({
        id: crypto.randomUUID(),
        po_line_id: li.id,
        description: li.description,
        material_id: li.material_id,
        qty_ordered: Number(li.qty),
        qty_received: Math.max(0, Number(li.qty) - Number(li.received_qty ?? 0)),
        unit: li.unit,
        unit_price: Number(li.unit_price),
        line_total: Number(li.line_total),
        condition: 'good' as GrLineCondition,
        discrepancy_notes: '',
      }))
    )
    if (selectedPo.delivery_address) setStorageLocation(selectedPo.delivery_address)
  }, [selectedPoId, selectedPo])

  const updateLine = (idx: number, patch: Partial<FormGrLineItem>) => {
    setLineItems((prev) => {
      const next = [...prev]
      next[idx] = { ...next[idx], ...patch }
      // Recompute line_total when qty changes
      const line = next[idx]
      next[idx].line_total = Math.round(Number(line.qty_received) * Number(line.unit_price) * 100) / 100
      return next
    })
  }

  const hasIssues = lineItems.some((l) => l.qty_received > 0 && l.condition !== 'good')

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return
    const valid = Array.from(incoming).filter((f) => f.size <= MAX_FILE_SIZE)
    setPackListFiles((prev) => {
      const existing = new Set(prev.map((f) => f.name + f.size))
      return [...prev, ...valid.filter((f) => !existing.has(f.name + f.size))]
    })
  }

  const removeFile = (idx: number) =>
    setPackListFiles((prev) => prev.filter((_, i) => i !== idx))

  // Lines actually being received (qty > 0); lines with 0 are skipped in this shipment
  const receivedLines = lineItems.filter((l) => l.qty_received > 0)

  // ── Validation ──────────────────────────────────────────────────────────────
  const errors: string[] = []
  if (submitted) {
    if (!selectedPo) errors.push('Select a Purchase Order')
    if (!receivedAt) errors.push('Enter the receipt date')
    if (!storageLocation.trim() && selectedPo && isPhysicalGr(selectedPo.type)) errors.push('Enter a storage location')
    if (receivedLines.length === 0) errors.push('At least one line must have a received quantity greater than 0')
    lineItems.forEach((l, i) => {
      if (l.qty_received < 0) errors.push(`Line ${i + 1}: Received quantity cannot be negative`)
      if (l.qty_received > 0 && (l.condition === 'discrepancy' || l.condition === 'damaged') && !l.discrepancy_notes?.trim()) {
        errors.push(`Line ${i + 1}: Discrepancy/damage notes are required`)
      }
    })
  }

  const handleSubmit = async () => {
    setSubmitted(true)
    if (!selectedPo) return

    const errs: string[] = []
    if (!receivedAt) errs.push('date')
    if (!storageLocation.trim() && isPhysicalGr(selectedPo.type)) errs.push('location')
    if (receivedLines.length === 0) errs.push('noLines')
    lineItems.forEach((l, i) => {
      if (l.qty_received > 0 && (l.condition === 'discrepancy' || l.condition === 'damaged') && !l.discrepancy_notes?.trim()) errs.push(`notes${i}`)
    })
    if (errs.length > 0) return

    try {
      const attachments: GrAttachmentIn[] = await Promise.all(
        packListFiles.map(async (f) => ({
          filename: f.name,
          content_type: f.type || 'application/octet-stream',
          data: await fileToBase64(f),
        }))
      )
      const newGr = await createGr.mutateAsync({
        // gr_type and vendor_id are derived from the PO server-side; line_total is
        // recomputed there too — send only the fields the user actually controls.
        title: selectedPo.title,
        currency: selectedPo.currency,
        po_id: selectedPo.id,
        storage_location: storageLocation.trim() || undefined,
        received_by: user?.name,
        line_items: receivedLines.map((l) => ({
          po_line_id: l.po_line_id,
          description: l.description,
          material_id: l.material_id || undefined,
          qty_ordered: l.qty_ordered,
          qty_received: l.qty_received,
          unit: l.unit,
          unit_price: l.unit_price,
          condition: l.condition,
          discrepancy_notes: l.discrepancy_notes || undefined,
        })),
        notes: notes.trim() || undefined,
        attachments: attachments.length > 0 ? attachments : undefined,
      })
      replaceTab(`/gr/${newGr.id}`)
    } catch {
      // error already in mutation state
    }
  }

  const filteredPos = eligiblePos.filter((p) => {
    if (!poSearch) return true
    const q = poSearch.toLowerCase()
    return p.number.toLowerCase().includes(q) || p.vendor_name.toLowerCase().includes(q) || p.title.toLowerCase().includes(q)
  })

  const totalValue = lineItems.reduce((s, l) => s + l.line_total, 0)

  return (
    <div className="flex flex-col gap-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to="/gr" className="text-neutral-400 hover:text-neutral-600 transition-colors">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">
            {selectedPo?.type === 4 ? 'Service Confirmation' : selectedPo?.type === 6 ? 'Project Completion' : 'New Goods Receipt'}
          </h1>
          <p className="mt-1 text-sm text-neutral-500">
            {selectedPo?.type === 4
              ? 'Confirm service completion against an approved Purchase Order'
              : selectedPo?.type === 6
                ? 'Confirm project completion against an approved Purchase Order'
                : 'Record received goods against an issued Purchase Order'}
          </p>
        </div>
      </div>

      {/* Error summary */}
      {submitted && errors.length > 0 && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 p-4">
          <p className="text-sm font-semibold text-danger-700 mb-1">Please fix the following issues:</p>
          <ul className="list-disc list-inside text-sm text-danger-600 space-y-0.5">
            {errors.map((e) => <li key={e}>{e}</li>)}
          </ul>
        </div>
      )}

      {/* PO Selection */}
      <div className="rounded-xl border border-neutral-200 bg-white p-6 flex flex-col gap-4">
        <h2 className="text-base font-semibold text-neutral-900">Purchase Order</h2>

        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-neutral-700">
            Select PO <span className="text-danger-600">*</span>
          </label>
          <div className="relative">
            <input
              type="text"
              placeholder="Search by PO number, vendor, or title..."
              value={selectedPo ? `${selectedPo.number} — ${selectedPo.vendor_name} — ${selectedPo.title}` : poSearch}
              onFocus={() => { if (!selectedPo) setShowPoDropdown(true) }}
              onChange={(e) => {
                setPoSearch(e.target.value)
                setSelectedPoId('')
                setShowPoDropdown(true)
              }}
              className={cn(
                'w-full h-10 px-3 rounded-lg border text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                submitted && !selectedPo ? 'border-danger-500' : 'border-neutral-300'
              )}
            />
            {selectedPo && (
              <button
                className="absolute right-2 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
                onClick={() => { setSelectedPoId(''); setPoSearch(''); setShowPoDropdown(true) }}
              >
                ×
              </button>
            )}
            {showPoDropdown && !selectedPo && (
              <div className="absolute z-10 mt-1 w-full rounded-lg border border-neutral-200 bg-white shadow-lg max-h-64 overflow-y-auto">
                {filteredPos.length === 0 ? (
                  <div className="px-4 py-3 text-sm text-neutral-400">No eligible POs found (must be issued, partially received, or approved service PO)</div>
                ) : (
                  filteredPos.map((p) => (
                    <button
                      key={p.id}
                      className="w-full text-left px-4 py-3 hover:bg-primary-50 border-b border-neutral-100 last:border-0"
                      onClick={() => {
                        setSelectedPoId(p.id)
                        setShowPoDropdown(false)
                        setPoSearch('')
                      }}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-sm text-neutral-900 font-mono">{p.number}</span>
                        <span className="text-xs text-neutral-500">{TYPE_LABELS[p.type]}</span>
                      </div>
                      <div className="text-xs text-neutral-500 mt-0.5">{p.vendor_name} · {p.title}</div>
                      <div className="text-xs text-neutral-400 mt-0.5">
                        Expected {formatDate(p.expected_delivery)} · {formatAmount(p.total, p.currency)}
                      </div>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
          {submitted && !selectedPo && (
            <p className="text-xs text-danger-600">Please select a Purchase Order</p>
          )}
        </div>

        {linkedPoNotReceivable && (
          <div className="rounded-lg border border-warning-200 bg-warning-50 p-4 flex gap-3">
            <AlertTriangle className="h-5 w-5 text-warning-500 flex-shrink-0 mt-0.5" />
            <div className="text-sm">
              <p className="font-semibold text-warning-700">
                {linkedPoNotReceivable.number} cannot take a goods receipt right now
              </p>
              <p className="text-warning-600 mt-0.5">
                It is in status <span className="font-medium">{linkedPoNotReceivable.status.replace(/_/g, ' ')}</span>.
                A receipt needs an issued or partially received PO — or an approved one for service and project POs.
                {' '}
                <Link to={`/po/${linkedPoNotReceivable.id}`} className="underline hover:text-warning-700">
                  Open the PO
                </Link>{' '}
                to check whether it has already been received, or pick another PO above.
              </p>
            </div>
          </div>
        )}

        {/* PO summary card */}
        {selectedPo && (
          <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4 grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
            <MetaRow label="Vendor" value={selectedPo.vendor_name} />
            <MetaRow label="Expected Delivery" value={formatDate(selectedPo.expected_delivery)} />
            <MetaRow label="Procurement Type" value={`Type ${selectedPo.type} — ${TYPE_LABELS[selectedPo.type]}`} />
            <MetaRow label="Total Value" value={formatAmount(selectedPo.total, selectedPo.currency)} mono />
            {selectedPo.delivery_address && <MetaRow label="Delivery Address" value={selectedPo.delivery_address} />}
          </div>
        )}
      </div>

      {selectedPo && (
        <>
          {/* Receipt Details */}
          <div className="rounded-xl border border-neutral-200 bg-white p-6 flex flex-col gap-4">
            <h2 className="text-base font-semibold text-neutral-900">Receipt Details</h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <FormField
                label="Receipt Date"
                required
                error={submitted && !receivedAt ? 'Required' : ''}
              >
                <input
                  type="date"
                  value={receivedAt}
                  onChange={(e) => setReceivedAt(e.target.value)}
                  className="w-full h-10 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                />
              </FormField>
              <FormField
                label="Storage Location / Bay"
                required={isPhysicalGr(selectedPo.type)}
                error={submitted && !storageLocation.trim() && isPhysicalGr(selectedPo.type) ? 'Required' : ''}
                hint={!isPhysicalGr(selectedPo.type) ? 'Not applicable for service / project GR' : ''}
              >
                <input
                  type="text"
                  placeholder="e.g. Bay 3A, Technical Warehouse"
                  value={storageLocation}
                  onChange={(e) => setStorageLocation(e.target.value)}
                  disabled={!isPhysicalGr(selectedPo.type)}
                  className="w-full h-10 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-400"
                />
              </FormField>
            </div>
            <FormField label="Notes" hint="Optional — any delivery notes, missing items, etc.">
              <textarea
                rows={2}
                placeholder="e.g. Items delivered to receiving dock B."
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                className="w-full px-3 py-2 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 resize-none"
              />
            </FormField>
          </div>

          {/* Pack List Attachments */}
          <div className="rounded-xl border border-neutral-200 bg-white p-6 flex flex-col gap-4">
            <div>
              <h2 className="text-base font-semibold text-neutral-900">Pack List / Supporting Documents</h2>
              <p className="mt-0.5 text-sm text-neutral-500">Optional — attach scanned packing lists, delivery notes, or photos (max 10 MB each)</p>
            </div>

            {/* Drop zone */}
            <div
              className={cn(
                'relative rounded-lg border-2 border-dashed transition-colors flex flex-col items-center justify-center gap-2 py-8 cursor-pointer',
                dragOver ? 'border-primary-400 bg-primary-50' : 'border-neutral-300 hover:border-neutral-400'
              )}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files) }}
              onClick={() => fileInputRef.current?.click()}
            >
              <Upload className="h-7 w-7 text-neutral-400" />
              <p className="text-sm font-medium text-neutral-600">Drop files here or click to browse</p>
              <p className="text-xs text-neutral-400">PDF, JPG, PNG, Excel — up to 10 MB each</p>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.jpg,.jpeg,.png,.xls,.xlsx"
                className="hidden"
                onChange={(e) => addFiles(e.target.files)}
              />
            </div>

            {/* File list */}
            {packListFiles.length > 0 && (
              <ul className="flex flex-col gap-2">
                {packListFiles.map((file, idx) => (
                  <li key={idx} className="flex items-center gap-3 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
                    <Paperclip className="h-4 w-4 text-neutral-400 shrink-0" />
                    <span className="flex-1 text-sm text-neutral-700 truncate">{file.name}</span>
                    <span className="text-xs text-neutral-400 shrink-0">{(file.size / 1024).toFixed(0)} KB</span>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); removeFile(idx) }}
                      className="text-neutral-400 hover:text-danger-600 transition-colors"
                      aria-label="Remove file"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Line Items */}
          <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
            <div className="px-6 py-4 border-b border-neutral-200 flex items-center justify-between">
              <h2 className="text-base font-semibold text-neutral-900">Items Received</h2>
              <span className="text-xs text-neutral-400">{lineItems.length} line{lineItems.length !== 1 ? 's' : ''}</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-neutral-200 bg-neutral-50">
                    <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 w-8">#</th>
                    <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Description</th>
                    <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500 w-24">Ordered</th>
                    <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500 w-28">Received Qty</th>
                    <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 w-16">Unit</th>
                    <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500 w-32">Line Total</th>
                    <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 w-48">Condition</th>
                  </tr>
                </thead>
                <tbody>
                  {lineItems.map((line, idx) => (
                    <LineRow
                      key={line.id}
                      line={line}
                      idx={idx}
                      currency={selectedPo.currency}
                      submitted={submitted}
                      onChange={(patch) => updateLine(idx, patch)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            {/* Totals */}
            <div className="px-6 py-4 border-t border-neutral-200 flex justify-end">
              <div className="min-w-56 space-y-1.5">
                <div className="flex justify-between text-sm text-neutral-500">
                  <span>Total Received Value</span>
                  <span className="font-mono font-semibold text-neutral-900">{formatAmount(totalValue, selectedPo.currency)}</span>
                </div>
                <div className="flex justify-between text-xs text-neutral-400">
                  <span>Original PO Total</span>
                  <span className="font-mono">{formatAmount(selectedPo.total, selectedPo.currency)}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Issues warning */}
          {hasIssues && (
            <div className="rounded-lg border border-warning-200 bg-warning-50 p-4 flex gap-3">
              <AlertTriangle className="h-5 w-5 text-warning-500 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-semibold text-warning-700">One or more lines have issues</p>
                <p className="text-sm text-warning-600 mt-0.5">
                  Submitting will flag this GR as Discrepancy and notify the Procurement Officer.
                  Invoice creation will be blocked until the discrepancy is resolved.
                </p>
              </div>
            </div>
          )}

          {!hasIssues && lineItems.length > 0 && (
            <div className="rounded-lg border border-success-200 bg-success-50 p-4 flex gap-3">
              <CheckCircle2 className="h-5 w-5 text-success-600 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-success-700">
                All items in good condition. After submission, the requester will be notified to acknowledge receipt.
              </p>
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center justify-between pt-2 pb-6">
            <Link to="/gr">
              <Button variant="secondary">Cancel</Button>
            </Link>
            <Button onClick={handleSubmit} className="gap-2">
              <Package className="h-4 w-4" />
              Submit Goods Receipt
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

// ─── Line row ─────────────────────────────────────────────────────────────────

function LineRow({
  line, idx, currency, submitted, onChange,
}: {
  line: FormGrLineItem
  idx: number
  currency: string
  submitted: boolean
  onChange: (p: Partial<FormGrLineItem>) => void
}) {
  const hasIssue = line.condition === 'discrepancy' || line.condition === 'damaged'
  const noteError = submitted && line.qty_received > 0 && hasIssue && !line.discrepancy_notes?.trim()
  const qtyError = submitted && line.qty_received < 0

  return (
    <>
      <tr className={cn('border-b border-neutral-100', idx % 2 === 1 && 'bg-neutral-50/50')}>
        <td className="px-4 py-3 text-center text-neutral-400 text-xs">{idx + 1}</td>
        <td className="px-4 py-3">
          <div className="text-neutral-800">{line.description}</div>
          {line.material_id && <div className="text-xs text-neutral-400 font-mono mt-0.5">{line.material_id}</div>}
        </td>
        <td className="px-4 py-3 text-right text-neutral-500 font-mono text-xs">
          {line.qty_ordered} {line.unit}
        </td>
        <td className="px-4 py-3 text-right">
          <div className="flex flex-col items-end gap-0.5">
            <input
              type="number"
              min={0}
              step="any"
              value={line.qty_received === 0 ? '' : line.qty_received}
              onChange={(e) => {
                const v = parseFloat(e.target.value)
                onChange({ qty_received: isNaN(v) ? 0 : v })
              }}
              placeholder="0"
              className={cn(
                'w-20 h-8 text-right px-2 rounded border text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary-600',
                qtyError ? 'border-danger-400 bg-danger-50' : 'border-neutral-300'
              )}
            />
            {qtyError && <span className="text-[10px] text-danger-600">Required</span>}
          </div>
        </td>
        <td className="px-4 py-3 text-neutral-500 text-xs">{line.unit}</td>
        <td className="px-4 py-3 text-right font-mono text-xs font-semibold text-neutral-800">
          {formatAmount(line.line_total, currency)}
        </td>
        <td className="px-4 py-3">
          <div className="flex gap-3">
            {CONDITION_OPTIONS.map((opt) => (
              <label key={opt.value} className={cn('flex items-center gap-1 cursor-pointer text-xs', opt.color)}>
                <input
                  type="radio"
                  name={`condition-${line.id}`}
                  value={opt.value}
                  checked={line.condition === opt.value}
                  onChange={() => onChange({ condition: opt.value })}
                  className="h-3.5 w-3.5"
                />
                {opt.label}
              </label>
            ))}
          </div>
        </td>
      </tr>
      {/* Discrepancy details row */}
      {hasIssue && (
        <tr className={cn('border-b border-neutral-100', idx % 2 === 1 && 'bg-neutral-50/50')}>
          <td />
          <td colSpan={6} className="px-4 pb-3">
            <div className="rounded-lg border border-warning-200 bg-warning-50 p-3 flex flex-col gap-2">
              <label className="text-xs font-medium text-warning-700">
                {line.condition === 'damaged' ? 'Damage' : 'Discrepancy'} Notes <span className="text-danger-600">*</span>
              </label>
              <textarea
                rows={2}
                placeholder="Describe the issue — quantity short, damage details, etc."
                value={line.discrepancy_notes ?? ''}
                onChange={(e) => onChange({ discrepancy_notes: e.target.value })}
                className={cn(
                  'w-full px-3 py-1.5 rounded border text-sm bg-white focus:outline-none focus:ring-1 focus:ring-warning-400 resize-none',
                  noteError ? 'border-danger-400' : 'border-warning-300'
                )}
              />
              {noteError && <p className="text-xs text-danger-600">Notes are required for discrepancy/damage</p>}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function FormField({
  label, required, hint, error, children,
}: {
  label: string
  required?: boolean
  hint?: string
  error?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-neutral-700">
        {label}
        {required && <span className="text-danger-600 ml-1">*</span>}
      </label>
      {children}
      {hint && !error && <p className="text-xs text-neutral-400">{hint}</p>}
      {error && <p className="text-xs text-danger-600">{error}</p>}
    </div>
  )
}

function MetaRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="text-neutral-500 min-w-36 flex-shrink-0">{label}</span>
      <span className={cn('text-neutral-800 font-medium', mono && 'font-mono')}>{value}</span>
    </div>
  )
}
