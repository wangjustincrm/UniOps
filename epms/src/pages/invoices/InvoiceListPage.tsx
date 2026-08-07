import { useState, useRef, Fragment } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  Upload, Search, AlertTriangle, CheckCircle2, Clock,
  X, FileText, ChevronDown, ChevronUp, ExternalLink, Loader2, Plus, Trash2, UserPlus,
} from 'lucide-react'
import { parseInvoiceFile } from '@/lib/invoice-parser'
import { EXPENSE_BASE } from '@/lib/api'
import { createPortal } from 'react-dom'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Pagination } from '@/components/ui/Pagination'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { computeSla, type InvoiceStatus } from '@/stores/invoice.store'
import { useInvoices, useCreateInvoice, useMatchInvoice, useResolveException, useDeleteInvoice } from '@/hooks/useInvoices'
import { useCreateVendorCredit } from '@/hooks/useVendorCredits'
import { usePos } from '@/hooks/usePos'
import { useGrs } from '@/hooks/useGrs'
import { useVendors } from '@/hooks/useVendors'
import { useAuthStore } from '@/stores/auth.store'
import { useRolePermissions, useConfig } from '@/hooks/useConfig'
import type { ApiInvoice, InvoiceLineItem, AllocationInput, NonPoLineInput } from '@/services/invoices'
import type { ApiPo } from '@/services/po'
import { InvoiceAllocationPanel, type AllocationAssignment } from './InvoiceAllocationPanel'
import { MatchPanel } from './MatchPanel'
import { FilePreviewPanel } from './FilePreviewPanel'
import { AssignMatchDialog } from './AssignMatchDialog'

// Roles allowed to run the 3-way match (mirrors epms-api invoices.py _AP_ROLES,
// which gates POST /invoices/{id}/match). Users without one of these must not be
// offered the "Match to PO" action — the backend would 403.
const MATCH_ROLES = new Set(['system_admin', 'ap_clerk', 'finance_manager', 'finance_bp'])

// PO statuses an invoice can be matched/allocated against.
// closed excluded: closed POs (incl. PMS imports closed by PAID) never enter match candidates
const MATCHABLE_PO_STATUSES = ['issued', 'approved', 'partially_received', 'fully_received']

// ─── Status badge ─────────────────────────────────────────────────────────────

const STATUS_CFG: Record<InvoiceStatus, { label: string; variant: 'neutral' | 'warning' | 'info' | 'success' | 'danger'; dot: string }> = {
  unmatched:    { label: 'Unmatched',       variant: 'warning', dot: 'bg-warning-500'  },
  matched:      { label: 'Matched',         variant: 'success', dot: 'bg-success-600'  },
  exception:    { label: 'Exception',       variant: 'danger',  dot: 'bg-danger-600'   },
  match_review: { label: 'Pending Review',  variant: 'info',    dot: 'bg-primary-500'  },
  approved:     { label: 'Approved',        variant: 'success', dot: 'bg-success-600'  },
  paid:         { label: 'Paid',            variant: 'neutral', dot: 'bg-neutral-400'  },
}

function InvoiceStatusBadge({ status }: { status: InvoiceStatus }) {
  const c = STATUS_CFG[status]
  return (
    <Badge variant={c.variant}>
      <span className={cn('size-1.5 rounded-full', c.dot)} />
      {c.label}
    </Badge>
  )
}

// ─── SLA badge ────────────────────────────────────────────────────────────────

function SlaBadge({ uploadedAt }: { uploadedAt: string }) {
  const { days, status } = computeSla(uploadedAt)
  if (status === 'overdue')
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-danger-50 px-2 py-0.5 text-xs font-medium text-danger-700">
        <AlertTriangle className="h-3 w-3" /> {days}d overdue
      </span>
    )
  if (status === 'warning')
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning-50 px-2 py-0.5 text-xs font-medium text-warning-700">
        <Clock className="h-3 w-3" /> Due today
      </span>
    )
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-success-50 px-2 py-0.5 text-xs font-medium text-success-700">
      <CheckCircle2 className="h-3 w-3" /> On time
    </span>
  )
}

// ─── AI badge ─────────────────────────────────────────────────────────────────

function AiBadge() {
  return (
    <span className="inline-flex items-center rounded-full bg-primary-50 border border-primary-200 px-1.5 py-0.5 text-[10px] font-medium text-primary-600 ml-1.5">
      AI
    </span>
  )
}

// ─── Upload modal ─────────────────────────────────────────────────────────────

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

interface UploadModalProps {
  onClose: () => void
  // `kind` tells the caller which collection the id belongs to. A vendor credit
  // id is NOT an invoice id — routing one to /invoices/:id lands on "Invoice not
  // found" and pushes the operator into re-uploading a credit that was in fact
  // created (409 duplicate). Defaults to 'invoice' for the many invoice-path
  // call sites below.
  onUploaded: (id: string, kind?: 'invoice' | 'credit') => void
}

/** Display-time sign derivation for the amount fields — NOT a normalisation
 *  point. The single normalisation of a credit's sign lives server-side in
 *  finance-api app/crud/vendor_credit.py `_positive()`. Here we only choose
 *  what to show: a credit note is entered as a positive figure, an invoice
 *  keeps whatever OCR parsed (including a negative, so the `amtNum <= 0`
 *  guard can fire). */
const displayAmount = (raw: number, kind: 'invoice' | 'credit_note'): string =>
  String(kind === 'credit_note' ? Math.abs(raw) : raw)

function UploadModal({ onClose, onUploaded }: UploadModalProps) {
  const { data: invoicesData } = useInvoices()
  const invoices = invoicesData?.items ?? []
  const { data: posData } = usePos()
  const pos = posData?.items ?? []
  const { data: grsData } = useGrs()
  const grs = grsData?.items ?? []
  const createInvoice = useCreateInvoice()
  const createVendorCredit = useCreateVendorCredit()
  const matchInvoiceMutation = useMatchInvoice()
  const fileRef = useRef<HTMLInputElement>(null)

  const [dragging, setDragging] = useState(false)
  const [file, setFile] = useState<{ name: string; size: string; raw: File } | null>(null)
  const [selectedVendor, setSelectedVendor] = useState<{ id: string; name: string; code: string } | null>(null)
  const [vendorQuery, setVendorQuery] = useState('')
  const [vendorOpen, setVendorOpen] = useState(false)

  const { data: vendorsData } = useVendors({ search: vendorQuery || undefined, active_only: true, page_size: 50 })
  const vendorOptions = vendorsData?.items ?? []
  const [vendorInvoiceNumber, setVendorInvoiceNumber] = useState('')
  const [poNumber, setPoNumber] = useState('')
  const [invoiceDate, setInvoiceDate] = useState(new Date().toISOString().slice(0, 10))
  const [dueDate, setDueDate] = useState('')
  const [amount, setAmount] = useState('')
  const [docType, setDocType] = useState<'invoice' | 'credit_note'>('invoice')
  const [docTypeAutoDetected, setDocTypeAutoDetected] = useState(false)
  const [taxAmount, setTaxAmount] = useState('')
  // The header figures exactly as OCR parsed them, kept untouched so switching
  // the document type can re-derive the displayed amounts from the source of
  // truth rather than from an already-abs()'d value. Without this, a credit
  // note detected at -0.04 stays 0.04 after the user switches back to "Regular
  // Invoice" and sails through epms-api's InvoiceCreate.amount gt=0 backstop.
  const [rawAmount, setRawAmount] = useState<number | null>(null)
  const [rawTax,    setRawTax]    = useState<number | null>(null)
  const [currency, setCurrency] = useState('CAD')
  const [notes, setNotes] = useState('')
  const [lineItems, setLineItems] = useState<InvoiceLineItem[]>([])
  // Set after create when a recognized PO leads into the allocation step (plan B):
  // the invoice exists, and the user confirms line-level allocations to match it.
  const [createdInv, setCreatedInv] = useState<ApiInvoice | null>(null)
  const [showAssignDialog, setShowAssignDialog] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  // AI parsing state
  const [parsing,       setParsing]       = useState(false)
  const [parseError,    setParseError]    = useState<string | null>(null)
  const [aiFields,      setAiFields]      = useState<Set<string>>(new Set())
  const [vendorHint,    setVendorHint]    = useState<string | null>(null)
  const [netTermsHint,  setNetTermsHint]  = useState<string | null>(null)

  const handleFile = async (f: File) => {
    setFile({ name: f.name, size: fmtSize(f.size), raw: f })
    setParseError(null)
    setAiFields(new Set())
    setVendorHint(null)
    setNetTermsHint(null)
    setLineItems([])
    setDocType('invoice')
    setDocTypeAutoDetected(false)
    setRawAmount(null)
    setRawTax(null)

    setParsing(true)
    try {
      const result = await parseInvoiceFile(f)
      if (!result.ok) { setParseError(result.error); return }

      const { fields } = result
      const filled = new Set<string>()

      // Two independent signals, OR'd: what the model called it, and the
      // structural fact of a negative total. Either one flips the form.
      //
      // The structural test is HEADER-ONLY — the pre-tax amount, or pre-tax
      // plus tax, being negative. It deliberately does NOT look at line items:
      // an ordinary payable invoice routinely carries a negative line. Return
      // and discount rows arrive from OCR as `quantity: -1`, and expense-api's
      // _normalize_negative_quantities() (app/services/ocr_service.py) flips
      // that sign onto unit_price, leaving the line amount negative under a
      // positive header. Keying on lines would flip such an invoice into
      // Credit Note mode under a banner claiming the total is negative, and an
      // accepted default would record a payable as a credit — it would never
      // reach the payment queue, and would later net down a real payment.
      const headerTotal = fields.amount === null
        ? null
        : fields.amount + (fields.taxAmount ?? 0)
      const looksNegative =
        (fields.amount !== null && fields.amount < 0) ||
        (headerTotal !== null && headerTotal < 0)
      const detected = fields.documentType === 'credit_note' || looksNegative
      setDocType(detected ? 'credit_note' : 'invoice')
      setDocTypeAutoDetected(detected)

      if (fields.vendorName) {
        setVendorQuery(fields.vendorName)
        setVendorOpen(true)
        setVendorHint(`AI extracted: "${fields.vendorName}"`)
        filled.add('vendorName')
      }

      if (fields.vendorInvoiceNumber) { setVendorInvoiceNumber(fields.vendorInvoiceNumber); filled.add('vendorInvoiceNumber') }
      if (fields.poNumber)            { setPoNumber(fields.poNumber);                        filled.add('poNumber') }

      const resolvedInvoiceDate = fields.invoiceDate ?? new Date().toISOString().slice(0, 10)
      if (fields.invoiceDate) { setInvoiceDate(fields.invoiceDate); filled.add('invoiceDate') }

      // Due date: explicit date takes priority; otherwise compute from NET XX terms.
      // Date math is done in UTC (parse Y-M-D → Date.UTC → setUTCDate) so it never
      // drifts by a day across timezones — parsing "YYYY-MM-DD" with new Date() is
      // UTC midnight, but getDate/setDate are local, and mixing them is off-by-one.
      if (fields.dueDate) {
        setDueDate(fields.dueDate)
        filled.add('dueDate')
      } else if (fields.paymentTermsNetDays !== null && fields.paymentTermsNetDays > 0) {
        const [y, m, d] = resolvedInvoiceDate.split('-').map(Number)
        const base = new Date(Date.UTC(y, m - 1, d))
        base.setUTCDate(base.getUTCDate() + fields.paymentTermsNetDays)
        const computed = base.toISOString().slice(0, 10)
        setDueDate(computed)
        filled.add('dueDate')
        setNetTermsHint(`Calculated from NET ${fields.paymentTermsNetDays} terms (issue date + ${fields.paymentTermsNetDays} days)`)
      }

      const kind = detected ? 'credit_note' : 'invoice'
      setRawAmount(fields.amount)
      setRawTax(fields.taxAmount)
      if (fields.amount    !== null)  { setAmount(displayAmount(fields.amount, kind)); filled.add('amount') }
      if (fields.taxAmount !== null)  { setTaxAmount(displayAmount(fields.taxAmount, kind)); filled.add('taxAmount') }
      if (fields.currency && ['CAD','USD','EUR','RMB'].includes(fields.currency)) {
        setCurrency(fields.currency); filled.add('currency')
      }

      // Line items
      if (fields.lineItems && fields.lineItems.length > 0) {
        // Same normalisation point as the amount/taxAmount prefill above — the
        // only place OCR sign-flipping happens. A uniform negation (not abs()):
        // credit notes can legitimately mix signs (e.g. +100 credit, -10
        // restocking fee, header -90); negating every line by -1 preserves the
        // relative signs and keeps quantity * unit_price === line_total, while
        // still summing to the now-positive header. abs() would break that —
        // it would turn +100/-10 into 100/10, summing to 110, not 90.
        const headerWasNegative = fields.amount !== null && fields.amount < 0
        const neg = (n: number) => (n === 0 ? 0 : -n)   // avoid -0
        const items = detected && headerWasNegative
          ? fields.lineItems.map((li) => ({ ...li, unit_price: neg(li.unit_price), line_total: neg(li.line_total) }))
          : fields.lineItems
        setLineItems(items)
        filled.add('lineItems')
      }

      setAiFields(filled)
    } finally {
      setParsing(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) void handleFile(f)
  }

  // The only way the document type changes after parsing. Re-deriving the
  // displayed amounts from rawAmount/rawTax (what OCR actually read off the
  // header) is what makes the switch reversible: flipping back to "Regular
  // Invoice" restores the negative figure, so the `amtNum <= 0` guard below
  // fires again instead of letting a mis-classified credit through as a
  // positive invoice. No-ops when there is nothing parsed (manual entry).
  const changeDocType = (next: 'invoice' | 'credit_note') => {
    setDocType(next)
    if (rawAmount !== null) setAmount(displayAmount(rawAmount, next))
    if (rawTax    !== null) setTaxAmount(displayAmount(rawTax, next))
  }

  const amtNum = parseFloat(amount) || 0
  const taxNum = parseFloat(taxAmount) || 0
  const total  = amtNum + taxNum

  // Kept as a separate boolean (not inlined into the JSX ternary condition):
  // if the ternary's test were `docType === 'credit_note'` directly, TS
  // control-flow narrowing would pin `docType` to the literal 'credit_note'
  // inside that branch, making the `docType === 'invoice'` radio check below
  // a "no overlap" type error.
  const showCreditNoteBanner = docTypeAutoDetected && docType === 'credit_note'

  // Live PO lookup by PO number
  const matchedPo: ApiPo | undefined = poNumber.trim()
    ? pos.find((p) =>
        p.number.toLowerCase() === poNumber.trim().toLowerCase() &&
        MATCHABLE_PO_STATUSES.includes(p.status)
      )
    : undefined

  // Duplicate check: same vendor + same vendor invoice number
  const isDuplicate =
    !!selectedVendor &&
    vendorInvoiceNumber.trim() !== '' &&
    invoices.some(
      (i) =>
        i.vendor_name.toLowerCase() === selectedVendor.name.toLowerCase() &&
        i.vendor_invoice_number.toLowerCase() === vendorInvoiceNumber.trim().toLowerCase()
    )

  // Greedily pair each invoice line with an unused PO line of equal pre-tax amount.
  // Returns line-level allocations ONLY if every invoice line is paired AND they
  // sum to the invoice pre-tax total (a clean line-level match); else null.
  const buildLineLevelAllocations = (inv: ApiInvoice, po: ApiPo): AllocationInput[] | null => {
    const invLines = inv.line_items ?? []
    if (invLines.length === 0) return null
    const used = new Set<string>()
    const allocs: AllocationInput[] = []
    for (const l of invLines) {
      if (!l.id) return null
      const amt = Number(l.line_total)
      const target = po.line_items.find((pl) => !used.has(pl.id) && Math.abs(Number(pl.line_total) - amt) < 0.01)
      if (!target) return null
      used.add(target.id)
      allocs.push({ invoice_line_id: l.id, po_id: po.id, po_line_id: target.id, allocated_amount: amt, allocated_tax: 0 })
    }
    const sum = allocs.reduce((s, a) => s + a.allocated_amount, 0)
    if (Math.abs(sum - Number(inv.amount)) > 0.01) return null
    return allocs
  }

  const handleSubmit = async () => {
    setSubmitted(true)
    setSubmitError(null)
    if (!selectedVendor || !vendorInvoiceNumber.trim() || !invoiceDate || !amount) return
    if (amtNum <= 0) {
      setSubmitError(
        docType === 'credit_note'
          ? 'Credit amount must be greater than zero.'
          : 'Negative total — this looks like a Credit Note. Switch the document type above.',
      )
      return
    }
    // isDuplicate checks vendorInvoiceNumber against the regular-invoice list —
    // not meaningful for credit notes, whose numbers live in a separate
    // namespace. finance-api does its own duplicate detection for credits
    // (409 with the existing credit named), surfaced in the catch below.
    if (docType === 'invoice' && isDuplicate) return

    if (docType === 'credit_note') {
      try {
        const credit = await createVendorCredit.mutateAsync({
          vendor_id: selectedVendor.id,
          vendor_name: selectedVendor.name,
          vendor_credit_number: vendorInvoiceNumber.trim(),
          credit_date: invoiceDate,
          currency,
          amount: amtNum,
          tax_amount: taxNum,
          po_number: poNumber.trim() || null,
          line_items: lineItems,
          file_name: file?.name ?? null,
          notes: notes.trim() || undefined,
        })
        if (file?.raw) {
          const form = new FormData()
          form.append('file', file.raw)
          const token = useAuthStore.getState().token
          // Non-fatal — the credit is already created — but a non-OK response
          // must still be reported. Phase A offers no other way to view the
          // document, so silently swallowing a 413/500 leaves a credit whose
          // file_name names a file that was never stored. Mirrors the invoice
          // branch below.
          await fetch(
            `${EXPENSE_BASE}/api/v1/invoice-attachments?invoice_id=${credit.id}&invoice_source=credit`,
            { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: form },
          ).then((res) => {
            if (!res.ok) console.error('Credit note attachment upload failed:', res.status)
          }).catch((e) => console.error('Credit note attachment upload failed:', e))
        }
        onUploaded(credit.id, 'credit')
      } catch (err) {
        const status = (err as { status?: number }).status
        setSubmitError(
          status === 409
            ? `${(err as Error).message}. Open Vendor Credits to review the existing entry.`
            : err instanceof Error ? err.message : 'Upload failed',
        )
      }
      return
    }

    try {
      const inv = await createInvoice.mutateAsync({
        vendor_invoice_number: vendorInvoiceNumber.trim(),
        vendor_id: selectedVendor.id,
        amount: amtNum,
        tax_amount: taxNum,
        currency,
        invoice_date: invoiceDate,
        due_date: dueDate || new Date(new Date(invoiceDate).getTime() + 30 * 86400000).toISOString().slice(0, 10),
        notes: notes.trim() || undefined,
        line_items: lineItems.length > 0 ? lineItems : undefined,
      })

      // Save file binary as attachment in unified invoice storage (expense-api)
      if (file?.raw) {
        try {
          const form = new FormData()
          form.append('file', file.raw)
          const token = useAuthStore.getState().token
          await fetch(
            `${EXPENSE_BASE}/api/v1/invoice-attachments?invoice_id=${inv.id}&invoice_source=epms`,
            { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: form },
          ).then((res) => {
            if (!res.ok) console.error('Invoice attachment upload failed:', res.status)
          }).catch((e) => console.error('Invoice attachment upload failed:', e))  // non-fatal — invoice was created
        } catch (e) { console.error('Invoice attachment upload error:', e) }
      }

      // A recognized PO → auto-match immediately (no second action). Line-level
      // when it balances cleanly, else whole-invoice total-value to that PO.
      // On any match error, fall back to the manual allocation panel.
      // Guard is intentionally `matchedPo` alone, not `&& canMatchInvoice`: the
      // uploader here is always the current user, and Feature #4 lets an
      // uploader match their own invoice regardless of canMatchInvoice's
      // (AP-oriented) scope. Any residual server-side denial (e.g. an edge
      // case canMatchInvoice doesn't model) is still caught by the
      // onError → manual panel fallback below, so this can't silently fail.
      if (matchedPo) {
        const lineAllocs = buildLineLevelAllocations(inv, matchedPo)
        const linkedGr = grs.find((g) => g.po_id === matchedPo.id && g.status !== 'cancelled')
        matchInvoiceMutation.mutate(
          lineAllocs
            ? { id: inv.id, allocations: lineAllocs, gr_id: linkedGr?.id }
            : { id: inv.id, po_id: matchedPo.id, gr_id: linkedGr?.id },
          {
            onSuccess: () => onUploaded(inv.id),
            onError: () => setCreatedInv(inv),   // fall back to manual panel
          },
        )
        return
      }
      onUploaded(inv.id)
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Upload failed')
    }
  }

  const fieldErr = (val: string) => submitted && !val.trim() ? 'border-danger-400' : 'border-neutral-300'

  // ── Step 2: line-level allocation (invoice created, PO recognized) ──────────
  if (createdInv) {
    // Candidate POs: the recognized PO first, then other open POs for the vendor.
    const allocPos: ApiPo[] = [
      ...(matchedPo ? [matchedPo] : []),
      ...pos.filter((p) =>
        p.id !== matchedPo?.id &&
        p.vendor_id === createdInv.vendor_id &&
        MATCHABLE_PO_STATUSES.includes(p.status)),
    ]

    // Prefill: greedily pair each invoice line with an unused PO line of the same
    // pre-tax amount on the recognized PO; single-line vs single-line pairs match
    // regardless of amount. Anything else is left for the user to drag.
    const prefill: AllocationAssignment = {}
    if (matchedPo) {
      const usedPoLines = new Set<string>()
      const invLines = createdInv.line_items ?? []
      for (const l of invLines) {
        if (!l.id) continue
        const amt = Number(l.line_total)
        const target = matchedPo.line_items.find((pl) =>
          !usedPoLines.has(pl.id) && Math.abs(Number(pl.line_total) - amt) < 0.01)
        if (target) {
          prefill[l.id] = { poId: matchedPo.id, poLineId: target.id }
          usedPoLines.add(target.id)
        }
      }
      if (Object.keys(prefill).length === 0 && invLines.length === 1 && invLines[0].id && matchedPo.line_items.length === 1) {
        prefill[invLines[0].id!] = { poId: matchedPo.id, poLineId: matchedPo.line_items[0].id }
      }
    }

    const handleAllocSubmit = (payload: { allocations: AllocationInput[]; nonPoLines: NonPoLineInput[]; referencePoId: string | null }) => {
      const linkedGr = matchedPo ? grs.find((g) => g.po_id === matchedPo.id && g.status !== 'cancelled') : undefined
      matchInvoiceMutation.mutate(
        { id: createdInv.id, allocations: payload.allocations, non_po_lines: payload.nonPoLines,
          reference_po_id: payload.referencePoId ?? undefined, gr_id: linkedGr?.id },
        { onSuccess: () => onUploaded(createdInv.id) },
      )
    }

    // Closing/skipping keeps the invoice — it stays in the queue as unmatched.
    const finishUnmatched = () => onUploaded(createdInv.id)

    return (
      <>
        {createPortal(
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
            <div className="w-full max-w-3xl max-h-[92vh] rounded-2xl bg-white shadow-2xl flex flex-col">
              <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4 shrink-0">
                <div className="flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-full bg-success-50">
                    <CheckCircle2 className="h-5 w-5 text-success-600" />
                  </div>
                  <div>
                    <h2 className="text-sm font-semibold text-neutral-900">Allocate to Purchase Order</h2>
                    <p className="text-xs text-neutral-400">
                      Invoice {createdInv.vendor_invoice_number} uploaded — confirm line allocations to complete the match
                    </p>
                  </div>
                </div>
                <button onClick={finishUnmatched} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100">
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 flex flex-col gap-4">
                <InvoiceAllocationPanel
                  invoice={createdInv}
                  pos={allocPos}
                  submitting={matchInvoiceMutation.isPending}
                  defaultAssignments={prefill}
                  onSubmit={handleAllocSubmit}
                />
                {matchInvoiceMutation.isError && (
                  <div className="flex items-center gap-2 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                    {matchInvoiceMutation.error instanceof Error ? matchInvoiceMutation.error.message : 'Match failed'}
                  </div>
                )}
                <div className="flex items-center gap-2 justify-start flex-wrap">
                  <Button variant="secondary" size="sm" onClick={finishUnmatched}>
                    Skip for now — leave unmatched
                  </Button>
                  <Button variant="secondary" size="sm" className="gap-1.5" onClick={() => setShowAssignDialog(true)}>
                    <UserPlus className="h-3.5 w-3.5" />
                    Assign to someone instead
                  </Button>
                </div>
              </div>
            </div>
          </div>,
          document.body
        )}
        {showAssignDialog && (
          <AssignMatchDialog
            invoiceId={createdInv.id}
            onClose={() => setShowAssignDialog(false)}
            onAssigned={() => { setShowAssignDialog(false); onUploaded(createdInv.id) }}
          />
        )}
      </>
    )
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className={cn(
        'w-full rounded-2xl bg-white shadow-2xl flex flex-col',
        file ? 'max-w-[90rem] h-[92vh]' : 'max-w-xl max-h-[92vh]'
      )}>
        {/* Header */}
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4 shrink-0">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-50">
              <Upload className="h-5 w-5 text-primary-600" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">Upload Invoice</h2>
              <p className="text-xs text-neutral-400">New invoice will be queued for PO matching</p>
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className={cn('flex-1 min-h-0 px-6 py-5 flex flex-col gap-4', !file && 'overflow-y-auto')}>
          {/* File drop zone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileRef.current?.click()}
            className={cn(
              'flex flex-col items-center gap-2 rounded-xl border-2 border-dashed py-6 cursor-pointer transition-colors shrink-0',
              dragging ? 'border-primary-400 bg-primary-50' : 'border-neutral-200 hover:border-primary-300 hover:bg-neutral-50'
            )}
          >
            {file ? (
              <div className="flex items-center gap-2 text-sm text-neutral-700">
                <FileText className="h-5 w-5 text-primary-600" />
                <span className="font-medium">{file.name}</span>
                <span className="text-neutral-400">{file.size}</span>
                {parsing && (
                  <span className="inline-flex items-center gap-1 text-xs text-primary-600">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Parsing…
                  </span>
                )}
                <button
                  onClick={(e) => { e.stopPropagation(); setFile(null); setAiFields(new Set()); setVendorHint(null); setParseError(null) }}
                  className="text-neutral-300 hover:text-danger-500"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ) : (
              <>
                <Upload className={cn('h-8 w-8', dragging ? 'text-primary-500' : 'text-neutral-300')} />
                <p className="text-sm text-neutral-500">
                  <span className="font-medium text-primary-600">Click to upload</span> or drag & drop
                </p>
                <p className="text-xs text-neutral-400">PDF, JPG, PNG · Max 25 MB</p>
              </>
            )}
            <input ref={fileRef} type="file" accept=".pdf,.jpg,.jpeg,.png" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void handleFile(f) }} />
          </div>

          {/* AI parse error */}
          {parseError && (
            <div className="flex shrink-0 items-center gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              AI parsing failed — please fill fields manually.{parseError && ` (${parseError})`}
            </div>
          )}

          {/* File selected: preview (left) + form (right). No file: `contents`
              makes both wrappers transparent so the layout is exactly as before. */}
          <div className={cn(file ? 'flex flex-1 min-h-0 gap-5' : 'contents')}>
          {file && (
            <div className="w-[60%] shrink-0">
              <FilePreviewPanel file={file.raw} />
            </div>
          )}
          <div className={cn(file ? 'flex-1 min-w-0 overflow-y-auto flex flex-col gap-4 pr-1' : 'contents')}>

          {/* Document type */}
          {showCreditNoteBanner ? (
            <div className="mb-4 rounded-lg border border-warning-300 bg-warning-50 p-3">
              <p className="text-sm font-medium text-warning-800">
                This looks like a Credit Note — the document total is negative.
              </p>
              <div className="mt-2 flex gap-4 text-sm">
                <label className="flex items-center gap-1.5">
                  {/* Cast: TS narrows docType to the 'credit_note' literal in this
                      branch (correctly — this block only renders when it is), which
                      makes a direct `docType === 'invoice'` comparison a no-overlap
                      type error. Widen back to compare; always evaluates false here,
                      same as the unnarrowed comparison would. */}
                  <input type="radio" checked={(docType as string) === 'invoice'}
                         onChange={() => changeDocType('invoice')} />
                  Regular Invoice
                </label>
                <label className="flex items-center gap-1.5">
                  <input type="radio" checked={docType === 'credit_note'}
                         onChange={() => changeDocType('credit_note')} />
                  Credit Note
                </label>
              </div>
            </div>
          ) : (
            <p className="mb-4 text-xs text-neutral-500">
              Document type: {docType === 'credit_note' ? 'Credit Note' : 'Invoice'}
              {' · '}
              <button type="button" className="text-primary-600 underline"
                      onClick={() => changeDocType(docType === 'invoice' ? 'credit_note' : 'invoice')}>
                Change
              </button>
            </p>
          )}

          {/* Vendor info */}
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-3">
              {/* Vendor search */}
              <div className="flex flex-col gap-1 relative">
                <label className="text-xs font-medium text-neutral-700 flex items-center">
                  Vendor <span className="text-danger-600 ml-0.5">*</span>
                  {aiFields.has('vendorName') && <AiBadge />}
                </label>
                <div className="relative">
                  <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-neutral-400" />
                  <input
                    type="text"
                    placeholder="Search vendor by name or code…"
                    value={selectedVendor ? selectedVendor.name : vendorQuery}
                    onFocus={() => { setVendorOpen(true); if (selectedVendor) setVendorQuery('') }}
                    onChange={(e) => { setVendorQuery(e.target.value); setSelectedVendor(null); setVendorOpen(true) }}
                    className={cn(
                      'h-9 w-full rounded-lg border pl-8 pr-8 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                      isDuplicate ? 'border-danger-400' : submitted && !selectedVendor ? 'border-danger-400' : 'border-neutral-300'
                    )}
                  />
                  {selectedVendor && (
                    <button
                      type="button"
                      onClick={() => { setSelectedVendor(null); setVendorQuery(''); setVendorHint(null) }}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
                {vendorHint && !selectedVendor && (
                  <p className="text-xs text-primary-500 mt-0.5">{vendorHint}</p>
                )}
                {submitted && !selectedVendor && <p className="text-xs text-danger-600">Select a vendor</p>}
                {vendorOpen && !selectedVendor && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setVendorOpen(false)} />
                    <div className="absolute top-full left-0 z-20 mt-1 w-full rounded-lg border border-neutral-200 bg-white py-1 shadow-lg max-h-44 overflow-y-auto">
                      {vendorOptions.map((v) => (
                        <button
                          key={v.id}
                          type="button"
                          onClick={() => { setSelectedVendor({ id: v.id, name: v.name, code: v.code }); setVendorOpen(false); setVendorQuery(''); setVendorHint(null); setAiFields((prev) => { const n = new Set(prev); n.delete('vendorName'); return n }) }}
                          className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-primary-50 text-left"
                        >
                          <span className="font-mono text-xs rounded bg-neutral-100 px-1.5 py-0.5 text-neutral-600">{v.code}</span>
                          {v.name}
                        </button>
                      ))}
                      {vendorOptions.length === 0 && (
                        <p className="px-3 py-2 text-xs text-neutral-400">No vendors found</p>
                      )}
                    </div>
                  </>
                )}
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-neutral-700 flex items-center">
                  {docType === 'credit_note' ? 'Credit Note #' : 'Vendor Invoice #'} <span className="text-danger-600 ml-0.5">*</span>
                  {aiFields.has('vendorInvoiceNumber') && <AiBadge />}
                </label>
                <input value={vendorInvoiceNumber}
                  onChange={(e) => {
                    setVendorInvoiceNumber(e.target.value)
                    setAiFields((prev) => { const n = new Set(prev); n.delete('vendorInvoiceNumber'); return n })
                  }}
                  placeholder="e.g. INV-2026-001"
                  className={cn('h-9 px-3 rounded-lg border text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                    isDuplicate ? 'border-danger-400' : fieldErr(vendorInvoiceNumber))} />
              </div>
            </div>
            {isDuplicate && (
              <div className="flex items-center gap-2 rounded-lg border border-danger-300 bg-danger-50 px-3 py-2 text-xs text-danger-700">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                <span>
                  Duplicate invoice — <span className="font-semibold">{vendorInvoiceNumber.trim()}</span> from{' '}
                  <span className="font-semibold">{selectedVendor?.name}</span> already exists in the system.
                  Please verify this invoice has not already been uploaded.
                </span>
              </div>
            )}
          </div>

          {/* PO Number */}
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-neutral-700 flex items-center">
              PO Number
              <span className="ml-1 text-xs font-normal text-neutral-400">(optional — enter to auto-match)</span>
              {aiFields.has('poNumber') && <AiBadge />}
            </label>
            <input
              value={poNumber}
              onChange={(e) => {
                setPoNumber(e.target.value)
                setAiFields((prev) => { const n = new Set(prev); n.delete('poNumber'); return n })
              }}
              placeholder="e.g. PO-ABC-2603-01"
              className="h-9 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
            />
            {docType === 'credit_note' && (
              <p className="mt-1 text-xs text-neutral-500">
                Optional — reference only. Credit notes are not 3-way matched.
              </p>
            )}
            {docType === 'invoice' && poNumber.trim() && (
              matchedPo ? (
                <div className="flex items-center gap-2 rounded-lg border border-success-300 bg-success-50 px-3 py-2 text-xs text-success-700">
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                  <span>
                    <span className="font-semibold">{matchedPo.number}</span>
                    {' — '}{matchedPo.vendor_name}
                    {' · '}{formatAmount(matchedPo.total, matchedPo.currency)}
                    {' · '}After upload you will allocate invoice lines to this PO to complete the match
                  </span>
                </div>
              ) : (
                <p className="text-xs text-warning-600 flex items-center gap-1">
                  <AlertTriangle className="h-3 w-3" />
                  PO not found or not in an issued/approved state — invoice will be queued as unmatched
                </p>
              )
            )}
          </div>

          {/* Dates */}
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-700 flex items-center">
                Invoice Date <span className="text-danger-600 ml-0.5">*</span>
                {aiFields.has('invoiceDate') && <AiBadge />}
              </label>
              <input type="date" value={invoiceDate}
                onChange={(e) => {
                  setInvoiceDate(e.target.value)
                  setAiFields((prev) => { const n = new Set(prev); n.delete('invoiceDate'); return n })
                }}
                className="h-9 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
            </div>
            {docType === 'invoice' && (
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-neutral-700 flex items-center">
                  Due Date
                  {aiFields.has('dueDate') && <AiBadge />}
                </label>
                <input type="date" value={dueDate}
                  onChange={(e) => {
                    setDueDate(e.target.value)
                    setNetTermsHint(null)
                    setAiFields((prev) => { const n = new Set(prev); n.delete('dueDate'); return n })
                  }}
                  className="h-9 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
                {netTermsHint && (
                  <p className="text-xs text-primary-500 mt-0.5">{netTermsHint}</p>
                )}
              </div>
            )}
          </div>

          {/* Amounts */}
          <div className="grid grid-cols-3 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-700 flex items-center">
                {docType === 'credit_note' ? 'Credit Amount (pre-tax)' : 'Amount (pre-tax)'} <span className="text-danger-600 ml-0.5">*</span>
                {aiFields.has('amount') && <AiBadge />}
              </label>
              <input type="number" min={0} step={0.01} value={amount}
                onChange={(e) => {
                  setAmount(e.target.value)
                  setAiFields((prev) => { const n = new Set(prev); n.delete('amount'); return n })
                }}
                placeholder="0.00"
                className={cn('h-9 px-3 rounded-lg border text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600',
                  submitted && amtNum <= 0 ? 'border-danger-400' : 'border-neutral-300')} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-700 flex items-center">
                {docType === 'credit_note' ? 'Credit Tax' : 'Tax Amount'}
                {aiFields.has('taxAmount') && <AiBadge />}
              </label>
              <input type="number" min={0} step={0.01} value={taxAmount}
                onChange={(e) => {
                  setTaxAmount(e.target.value)
                  setAiFields((prev) => { const n = new Set(prev); n.delete('taxAmount'); return n })
                }}
                placeholder="0.00"
                className="h-9 px-3 rounded-lg border border-neutral-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-700 flex items-center">
                Currency
                {aiFields.has('currency') && <AiBadge />}
              </label>
              <select value={currency}
                onChange={(e) => {
                  setCurrency(e.target.value)
                  setAiFields((prev) => { const n = new Set(prev); n.delete('currency'); return n })
                }}
                className="h-9 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600">
                {['CAD','USD','EUR','RMB'].map((c) => <option key={c}>{c}</option>)}
              </select>
            </div>
          </div>

          {total > 0 && (
            <div className="rounded-lg bg-neutral-50 border border-neutral-200 px-4 py-2.5 flex justify-between text-sm">
              <span className="text-neutral-500">Total Amount</span>
              <span className="font-mono font-semibold text-neutral-900">{formatAmount(total, currency)}</span>
            </div>
          )}

          {/* Line Items */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <label className="text-xs font-medium text-neutral-700 flex items-center">
                Line Items
                <span className="ml-1 text-xs font-normal text-neutral-400">(optional)</span>
                {aiFields.has('lineItems') && <AiBadge />}
              </label>
              <button
                type="button"
                onClick={() => setLineItems((prev) => [...prev, { description: '', quantity: 1, unit: null, unit_price: 0, line_total: 0 }])}
                className="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700 font-medium"
              >
                <Plus className="h-3.5 w-3.5" />
                Add Row
              </button>
            </div>
            {lineItems.length > 0 ? (
              <div className="rounded-lg border border-neutral-200 overflow-hidden">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-neutral-50 border-b border-neutral-200">
                      <th className="px-2 py-2 text-left font-semibold text-neutral-500">Description</th>
                      <th className="px-2 py-2 text-right font-semibold text-neutral-500 w-14">Qty</th>
                      <th className="px-2 py-2 text-left font-semibold text-neutral-500 w-16">Unit</th>
                      <th className="px-2 py-2 text-right font-semibold text-neutral-500 w-20">Unit Price</th>
                      <th className="px-2 py-2 text-right font-semibold text-neutral-500 w-20">Total</th>
                      <th className="w-8" />
                    </tr>
                  </thead>
                  <tbody>
                    {lineItems.map((item, idx) => (
                      <tr key={idx} className="border-b border-neutral-100 last:border-0">
                        <td className="px-2 py-1.5">
                          <input
                            value={item.description}
                            onChange={(e) => setLineItems((prev) => prev.map((it, i) => i === idx ? { ...it, description: e.target.value } : it))}
                            className="w-full h-7 px-2 rounded border border-neutral-200 text-xs focus:outline-none focus:ring-1 focus:ring-primary-500"
                            placeholder="Description"
                          />
                        </td>
                        <td className="px-2 py-1.5">
                          <input
                            type="number" min={0} step={0.001} value={item.quantity}
                            onChange={(e) => setLineItems((prev) => prev.map((it, i) => i === idx ? { ...it, quantity: parseFloat(e.target.value) || 0 } : it))}
                            className="w-full h-7 px-2 rounded border border-neutral-200 text-xs text-right font-mono focus:outline-none focus:ring-1 focus:ring-primary-500"
                          />
                        </td>
                        <td className="px-2 py-1.5">
                          <input
                            value={item.unit ?? ''}
                            onChange={(e) => setLineItems((prev) => prev.map((it, i) => i === idx ? { ...it, unit: e.target.value || null } : it))}
                            className="w-full h-7 px-2 rounded border border-neutral-200 text-xs focus:outline-none focus:ring-1 focus:ring-primary-500"
                            placeholder="pcs"
                          />
                        </td>
                        <td className="px-2 py-1.5">
                          <input
                            type="number" min={0} step={0.01} value={item.unit_price}
                            onChange={(e) => setLineItems((prev) => prev.map((it, i) => i === idx ? { ...it, unit_price: parseFloat(e.target.value) || 0 } : it))}
                            className="w-full h-7 px-2 rounded border border-neutral-200 text-xs text-right font-mono focus:outline-none focus:ring-1 focus:ring-primary-500"
                          />
                        </td>
                        <td className="px-2 py-1.5">
                          <input
                            type="number" min={0} step={0.01} value={item.line_total}
                            onChange={(e) => setLineItems((prev) => prev.map((it, i) => i === idx ? { ...it, line_total: parseFloat(e.target.value) || 0 } : it))}
                            className="w-full h-7 px-2 rounded border border-neutral-200 text-xs text-right font-mono focus:outline-none focus:ring-1 focus:ring-primary-500"
                          />
                        </td>
                        <td className="px-2 py-1.5 text-center">
                          <button
                            type="button"
                            onClick={() => setLineItems((prev) => prev.filter((_, i) => i !== idx))}
                            className="text-neutral-300 hover:text-danger-500"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-neutral-200 px-4 py-3 text-xs text-neutral-400 text-center">
                No line items · AI will extract them from the invoice, or add manually
              </div>
            )}
          </div>

          {/* Notes */}
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-neutral-700">Notes (optional)</label>
            <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)}
              placeholder="Any additional notes..."
              className="px-3 py-2 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 resize-none" />
          </div>

          </div>
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-neutral-100 px-6 py-4 flex flex-col gap-2 shrink-0">
          {submitError && (
            <div className="flex items-center gap-2 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              {submitError}
            </div>
          )}
          <div className="flex justify-end gap-3">
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button onClick={handleSubmit} disabled={createInvoice.isPending || createVendorCredit.isPending || matchInvoiceMutation.isPending} className="gap-2">
              <Upload className="h-4 w-4" />
              {createInvoice.isPending || createVendorCredit.isPending || matchInvoiceMutation.isPending
                ? 'Uploading…'
                : docType === 'credit_note' ? 'Upload Credit Note' : 'Upload Invoice'}
            </Button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}


// ─── Exception resolve panel ──────────────────────────────────────────────────

function ResolvePanel({ inv, onClose }: { inv: ApiInvoice; onClose: () => void }) {
  const resolveExceptionMutation = useResolveException()
  const [resolution, setResolution] = useState<'accepted' | 'credit_note_requested'>('accepted')
  const [note, setNote] = useState('')
  const [submitted, setSubmitted] = useState(false)

  const handleResolve = () => {
    setSubmitted(true)
    if (!note.trim()) return
    resolveExceptionMutation.mutate(
      { id: inv.id, resolution, note: note.trim() },
      {
        onSuccess: onClose,
        onError: (err) => {
          // error displayed below the button
          console.error('resolve exception failed:', err)
        },
      }
    )
  }

  return (
    <div className="mt-2 rounded-xl border border-warning-200 bg-warning-50 p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-warning-700">Resolve Exception</p>
        <button onClick={onClose} className="text-neutral-400 hover:text-neutral-600">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="rounded-lg border border-warning-200 bg-white p-3 text-xs">
        <p className="font-medium text-neutral-700 mb-1">Variance Details</p>
        <p className="text-neutral-500">{inv.exception_reason}</p>
      </div>

      <div className="flex gap-3">
        {([
          { value: 'accepted',               label: 'Accept with Justification' },
          { value: 'credit_note_requested',  label: 'Request Credit Note'       },
        ] as const).map((opt) => (
          <label key={opt.value} className={cn(
            'flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer text-xs transition-colors',
            resolution === opt.value
              ? 'border-primary-400 bg-primary-50 text-primary-700 font-medium'
              : 'border-neutral-200 text-neutral-500 hover:border-neutral-300'
          )}>
            <input type="radio" name="resolution" value={opt.value} checked={resolution === opt.value}
              onChange={() => setResolution(opt.value)} className="h-3 w-3" />
            {opt.label}
          </label>
        ))}
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-neutral-700">
          {resolution === 'accepted' ? 'Justification' : 'Instructions'} <span className="text-danger-600">*</span>
        </label>
        <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)}
          placeholder={resolution === 'accepted'
            ? 'Explain why the excess amount is acceptable...'
            : 'Instructions for the vendor to issue a credit note...'}
          className={cn(
            'px-3 py-2 rounded-lg border text-xs focus:outline-none focus:ring-1 focus:ring-primary-600 resize-none',
            submitted && !note.trim() ? 'border-danger-400' : 'border-neutral-300 bg-white'
          )} />
        {submitted && !note.trim() && <p className="text-xs text-danger-600">Required</p>}
      </div>

      {resolveExceptionMutation.isError && (
        <p className="text-xs text-danger-600 text-right">
          {resolveExceptionMutation.error instanceof Error
            ? resolveExceptionMutation.error.message
            : 'Failed to resolve exception'}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={resolveExceptionMutation.isPending}>Cancel</Button>
        <Button size="sm" onClick={handleResolve} disabled={resolveExceptionMutation.isPending}>
          {resolveExceptionMutation.isPending ? 'Submitting…' : 'Submit Resolution'}
        </Button>
      </div>
    </div>
  )
}

// ─── Tab content components ───────────────────────────────────────────────────

function UnmatchedTab() {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const { data } = useInvoices({ status: 'unmatched', page, page_size: pageSize })
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [assigningInv, setAssigningInv] = useState<ApiInvoice | null>(null)
  const deleteInvoice = useDeleteInvoice()
  const { user } = useAuthStore()
  const perms = useRolePermissions().data?.permissions
  const canDelete = user?.role === 'system_admin' || !!perms?.invoice_upload
  const isAp = !!user?.role && MATCH_ROLES.has(user.role)
  const canMatchInvoice = (inv: ApiInvoice) =>
    isAp || inv.uploaded_by === user?.id ||
    (inv.match_assignee_id != null && inv.match_assignee_id === user?.id)

  const unmatched = [...(data?.items ?? [])].sort(
    (a, b) => new Date(a.uploaded_at).getTime() - new Date(b.uploaded_at).getTime()
  )
  const total = data?.total ?? 0

  if (unmatched.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <CheckCircle2 className="h-10 w-10 text-success-300 mb-3" />
        <p className="text-sm font-medium text-neutral-500">No unmatched invoices</p>
        <p className="text-xs text-neutral-400 mt-1">All invoices have been matched to a PO</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="rounded-lg border border-warning-200 bg-warning-50 px-4 py-2.5 flex items-center gap-2">
        <Clock className="h-4 w-4 text-warning-500 flex-shrink-0" />
        <p className="text-xs text-warning-700">
          <span className="font-semibold">SLA: 2 business days</span> to match each invoice to a PO.
          Overdue items are escalated to the Finance Manager.
        </p>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              {['Vendor', 'Invoice #', 'Amount', 'Invoice Date', 'Upload Date', 'SLA', 'Action'].map((h) => (
                <th key={h} className={`px-4 py-3 text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap ${h === 'Amount' ? 'text-right' : 'text-left'}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {unmatched.map((inv, idx) => (
              <Fragment key={inv.id}>
                <tr className="border-b border-neutral-100 bg-white hover:bg-primary-50/60 transition-colors">
                  <td className="px-4 py-3 font-medium text-neutral-900">{inv.vendor_name}</td>
                  <td className="px-4 py-3">
                    <Link to={`/invoices/${inv.id}`} className="font-mono text-xs text-primary-600 hover:underline">
                      {inv.internal_ref}
                    </Link>
                    <div className="text-xs text-neutral-400 mt-0.5">{inv.vendor_invoice_number}</div>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs font-semibold text-neutral-900">
                    {formatAmount(inv.total_amount, inv.currency)}
                  </td>
                  <td className="px-4 py-3 text-neutral-600 text-xs">{formatDate(inv.invoice_date)}</td>
                  <td className="px-4 py-3 text-neutral-500 text-xs">{formatDate(inv.uploaded_at)}</td>
                  <td className="px-4 py-3"><SlaBadge uploadedAt={inv.uploaded_at} /></td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      {deletingId === inv.id ? (
                        <>
                          <span className="text-xs text-danger-600 font-medium">Delete?</span>
                          <button
                            onClick={() => deleteInvoice.mutate(inv.id, { onSuccess: () => setDeletingId(null) })}
                            disabled={deleteInvoice.isPending}
                            className="inline-flex items-center rounded-lg border border-danger-300 bg-danger-50 px-2.5 py-1 text-xs font-medium text-danger-700 hover:bg-danger-100 transition-colors disabled:opacity-50"
                          >
                            {deleteInvoice.isPending ? 'Deleting…' : 'Confirm'}
                          </button>
                          <button
                            onClick={() => setDeletingId(null)}
                            className="text-xs text-neutral-500 hover:text-neutral-700"
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <>
                          {inv.match_assignee_name && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-primary-50 border border-primary-200 px-2 py-0.5 text-[11px] font-medium text-primary-700">
                              <UserPlus className="h-3 w-3" />
                              {inv.match_assignee_name}
                            </span>
                          )}
                          {canMatchInvoice(inv) && (
                            <button
                              onClick={() => { setDeletingId(null); setExpandedId(expandedId === inv.id ? null : inv.id) }}
                              className="inline-flex items-center gap-1.5 rounded-lg border border-primary-300 bg-primary-50 px-2.5 py-1 text-xs font-medium text-primary-700 hover:bg-primary-100 transition-colors"
                            >
                              Match to PO
                              {expandedId === inv.id ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                            </button>
                          )}
                          {isAp && (
                            <button
                              onClick={() => { setExpandedId(null); setDeletingId(null); setAssigningInv(inv) }}
                              className="inline-flex items-center gap-1 rounded-lg border border-neutral-300 bg-white px-2.5 py-1 text-xs font-medium text-neutral-600 hover:bg-neutral-50 transition-colors"
                              title="Assign to someone"
                            >
                              <UserPlus className="h-3.5 w-3.5" />
                              {inv.match_assignee_name ? 'Reassign' : 'Assign'}
                            </button>
                          )}
                          {canDelete && (
                            <button
                              onClick={() => { setExpandedId(null); setDeletingId(inv.id) }}
                              className="rounded-lg p-1 text-neutral-300 hover:text-danger-500 hover:bg-danger-50 transition-colors"
                              title="Delete invoice"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </td>
                </tr>
                {expandedId === inv.id && (
                  <tr className="border-b border-neutral-100">
                    <td colSpan={7} className="px-4 pb-3">
                      <MatchPanel inv={inv} onClose={() => setExpandedId(null)} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      {assigningInv && (
        <AssignMatchDialog
          invoiceId={assigningInv.id}
          currentAssigneeName={assigningInv.match_assignee_name}
          onClose={() => setAssigningInv(null)}
          onAssigned={() => setAssigningInv(null)}
        />
      )}
    </div>
  )
}

function ExceptionsTab() {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const { data } = useInvoices({ status: 'exception', page, page_size: pageSize })
  const matchTolerancePct = useConfig().data?.invoice_match_tolerance_pct ?? 5
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const exceptions = data?.items ?? []
  const total = data?.total ?? 0

  if (exceptions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <CheckCircle2 className="h-10 w-10 text-success-300 mb-3" />
        <p className="text-sm font-medium text-neutral-500">No exceptions</p>
        <p className="text-xs text-neutral-400 mt-1">All matched invoices are within tolerance</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-2.5 flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-danger-600 flex-shrink-0" />
        <p className="text-xs text-danger-700">
          <span className="font-semibold">{exceptions.length} invoice(s)</span> have a variance exceeding {matchTolerancePct}% vs the linked PO.
          Each exception must be resolved before a Payment Application can be created.
        </p>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              {['Vendor', 'Invoice #', 'Invoice Amount', 'PO Total', 'Variance', 'Variance %', 'PO', 'Action'].map((h) => (
                <th key={h} className={`px-4 py-3 text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap ${['Invoice Amount','PO Total','Variance','Variance %'].includes(h) ? 'text-right' : 'text-left'}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {exceptions.map((inv, idx) => (
              <>
                <tr key={inv.id} className="border-b border-neutral-100 bg-white hover:bg-danger-50/30 transition-colors">
                  <td className="px-4 py-3 font-medium text-neutral-900">{inv.vendor_name}</td>
                  <td className="px-4 py-3">
                    <Link to={`/invoices/${inv.id}`} className="font-mono text-xs text-primary-600 hover:underline">
                      {inv.internal_ref}
                    </Link>
                    <div className="text-xs text-neutral-400 mt-0.5">{inv.vendor_invoice_number}</div>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs font-semibold text-neutral-900">
                    {formatAmount(inv.total_amount, inv.currency)}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-neutral-600">
                    {inv.po_total != null ? formatAmount(inv.po_total, inv.currency) : '—'}
                  </td>
                  <td className={cn('px-4 py-3 font-mono text-xs font-semibold', Number(inv.variance ?? 0) > 0 ? 'text-danger-600' : 'text-success-600')}>
                    {inv.variance != null ? `${Number(inv.variance) > 0 ? '+' : ''}${formatAmount(Number(inv.variance), inv.currency)}` : '—'}
                  </td>
                  <td className={cn('px-4 py-3 text-xs font-semibold', Math.abs(Number(inv.variance_pct) ?? 0) > matchTolerancePct ? 'text-danger-600' : 'text-success-600')}>
                    {inv.variance_pct != null ? `${Number(inv.variance_pct) > 0 ? '+' : ''}${Number(inv.variance_pct).toFixed(1)}%` : '—'}
                  </td>
                  <td className="px-4 py-3">
                    {inv.po_number
                      ? <Link to={`/po/${inv.po_id}`} className="font-mono text-xs text-primary-600 hover:underline inline-flex items-center gap-1">
                          {inv.po_number} <ExternalLink className="h-3 w-3" />
                        </Link>
                      : <span className="text-neutral-400">—</span>
                    }
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => setExpandedId(expandedId === inv.id ? null : inv.id)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-warning-300 bg-warning-50 px-2.5 py-1 text-xs font-medium text-warning-700 hover:bg-warning-100 transition-colors"
                    >
                      Resolve
                      {expandedId === inv.id ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                    </button>
                  </td>
                </tr>
                {expandedId === inv.id && (
                  <tr key={`${inv.id}-expand`} className="border-b border-neutral-100">
                    <td colSpan={8} className="px-4 pb-3">
                      <ResolvePanel inv={inv} onClose={() => setExpandedId(null)} />
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
    </div>
  )
}

function AllInvoicesTab() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<InvoiceStatus | 'all'>('all')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const deleteInvoice = useDeleteInvoice()
  const { user } = useAuthStore()
  const perms = useRolePermissions().data?.permissions
  const canDelete = user?.role === 'system_admin' || !!perms?.invoice_upload

  const { data } = useInvoices({
    search: search || undefined,
    status: statusFilter !== 'all' ? statusFilter : undefined,
    page,
    page_size: pageSize,
  })
  const filtered = [...(data?.items ?? [])].sort(
    (a, b) => new Date(b.uploaded_at).getTime() - new Date(a.uploaded_at).getTime()
  )
  const total = data?.total ?? 0

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-neutral-400" />
          <input type="text" placeholder="Search by ref, vendor, PO#..." value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            className="w-full h-9 pl-9 pr-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(['all', 'unmatched', 'matched', 'match_review', 'exception', 'approved', 'paid'] as const).map((s) => (
            <button key={s} onClick={() => { setStatusFilter(s); setPage(1) }}
              className={cn('px-3 py-1 rounded-full text-xs font-medium transition-colors capitalize',
                statusFilter === s ? 'bg-primary-600 text-white' : 'bg-neutral-100 text-neutral-600 hover:bg-neutral-200')}>
              {s === 'all' ? 'All' : s === 'match_review' ? 'Pending Review' : s}
            </button>
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16">
            <FileText className="h-8 w-8 text-neutral-300 mb-2" />
            <p className="text-sm text-neutral-400">No invoices found</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                {['Ref', 'Vendor', 'Invoice #', 'Amount', 'Invoice Date', 'Upload Date', 'Status', 'PO', ''].map((h) => (
                  <th key={h} className={`px-4 py-3 text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap ${h === 'Amount' ? 'text-right' : 'text-left'}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((inv) => (
                <tr key={inv.id} className="border-b border-neutral-100 bg-white hover:bg-primary-50/60 transition-colors">
                  <td className="px-4 py-3">
                    <Link to={`/invoices/${inv.id}`} className="font-mono text-xs text-primary-600 hover:underline">
                      {inv.internal_ref}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-neutral-700">{inv.vendor_name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-neutral-500">{inv.vendor_invoice_number}</td>
                  <td className="px-4 py-3 font-mono text-xs font-semibold text-neutral-900">
                    {formatAmount(inv.total_amount, inv.currency)}
                  </td>
                  <td className="px-4 py-3 text-neutral-600 text-xs">{formatDate(inv.invoice_date)}</td>
                  <td className="px-4 py-3 text-neutral-500 text-xs">{formatDate(inv.uploaded_at)}</td>
                  <td className="px-4 py-3"><InvoiceStatusBadge status={inv.status} /></td>
                  <td className="px-4 py-3">
                    {inv.po_number
                      ? <Link to={`/po/${inv.po_id}`} className="font-mono text-xs text-primary-600 hover:underline">{inv.po_number}</Link>
                      : <span className="text-neutral-300">—</span>
                    }
                  </td>
                  <td className="px-4 py-3 text-right">
                    {canDelete && (inv.status === 'unmatched' || inv.status === 'exception') && (
                      deletingId === inv.id ? (
                        <div className="flex items-center justify-end gap-2">
                          <span className="text-xs text-danger-600 font-medium">Delete?</span>
                          <button
                            onClick={() => deleteInvoice.mutate(inv.id, { onSuccess: () => setDeletingId(null) })}
                            disabled={deleteInvoice.isPending}
                            className="text-xs font-medium text-danger-600 hover:text-danger-800 disabled:opacity-50"
                          >
                            {deleteInvoice.isPending ? 'Deleting…' : 'Confirm'}
                          </button>
                          <button onClick={() => setDeletingId(null)} className="text-xs text-neutral-400 hover:text-neutral-600">
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setDeletingId(inv.id)}
                          className="rounded p-1 text-neutral-300 hover:text-danger-500 hover:bg-danger-50 transition-colors"
                          title="Delete invoice"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

type Tab = 'unmatched' | 'exceptions' | 'all' | 'from_oa'

// ─── OA Invoices tab ──────────────────────────────────────────────────────────

interface OaInvoiceItem {
  id: string; source: string; invoice_number: string | null
  vendor_name: string | null; total_amount: number; currency: string
  invoice_date: string | null; status: string; file_name: string | null
  created_at: string; pa_number: string | null; attachment_count: number
}

function OaInvoicesTab() {
  const { token } = useAuthStore()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const { data, isLoading } = useQuery<{ items: OaInvoiceItem[]; total: number }>({
    queryKey: ['oa-invoices', search, page, pageSize],
    queryFn: async () => {
      const qs = new URLSearchParams({ source: 'oa', page: String(page), page_size: String(pageSize) })
      if (search) qs.set('search', search)
      const res = await fetch(`${EXPENSE_BASE}/api/v1/invoices/all?${qs}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json()
    },
  })

  const OA_STATUS_COLORS: Record<string, string> = {
    uploaded: 'bg-neutral-100 text-neutral-600',
    reviewed: 'bg-blue-50 text-blue-700',
    used:     'bg-green-50 text-green-700',
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 max-w-xs">
        <Search className="h-4 w-4 text-neutral-400 shrink-0" />
        <input className="flex-1 text-sm focus:outline-none" placeholder="Search vendor or invoice #…"
          value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
        {search && <button onClick={() => { setSearch(''); setPage(1) }} className="text-neutral-300 hover:text-neutral-500"><X className="h-3.5 w-3.5" /></button>}
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-neutral-400">Loading OA invoices…</div>
        ) : !data?.items.length ? (
          <div className="py-10 text-center text-sm text-neutral-400">No OA invoices found.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                {['Invoice #', 'Vendor', 'Amount', 'Status', 'PA', 'Attachments', 'Date'].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.items.map((inv, i) => (
                <tr key={inv.id} className={cn('border-b border-neutral-100', i === data.items.length - 1 && 'border-b-0')}>
                  <td className="px-4 py-3 font-mono text-xs text-primary-600">{inv.invoice_number ?? '—'}</td>
                  <td className="px-4 py-3 text-neutral-800 max-w-[180px] truncate">{inv.vendor_name ?? '—'}</td>
                  <td className="px-4 py-3 font-mono">{formatAmount(inv.total_amount, inv.currency)}</td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', OA_STATUS_COLORS[inv.status] ?? 'bg-neutral-100 text-neutral-600')}>
                      {inv.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-neutral-500">{inv.pa_number ?? '—'}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500 text-center">
                    {inv.attachment_count > 0
                      ? <span className="inline-flex items-center gap-1 text-primary-600 font-medium"><FileText className="h-3.5 w-3.5" />{inv.attachment_count}</span>
                      : '—'
                    }
                  </td>
                  <td className="px-4 py-3 text-neutral-500">{formatDate(inv.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {(data?.total ?? 0) > 0 && (
          <Pagination
            page={page}
            pageSize={pageSize}
            total={data!.total}
            onPageChange={setPage}
            onPageSizeChange={(s) => { setPageSize(s); setPage(1) }}
          />
        )}
      </div>
    </div>
  )
}

export default function InvoiceListPage() {
  const { data: allData } = useInvoices()
  const invoices = allData?.items ?? []
  const { user } = useAuthStore()
  const perms = useRolePermissions().data?.permissions
  const navigate = useNavigate()

  const [activeTab, setActiveTab] = useState<Tab>('all')
  const [showUpload, setShowUpload] = useState(false)

  const canUpload = user?.role === 'system_admin' || !!perms?.invoice_upload

  const unmatchedCount  = invoices.filter((i) => i.status === 'unmatched').length
  const exceptionCount  = invoices.filter((i) => i.status === 'exception').length

  const tabs: { key: Tab; label: string; count?: number; danger?: boolean }[] = [
    { key: 'all',        label: 'All Invoices' },
    { key: 'unmatched',  label: 'Unmatched Queue', count: unmatchedCount, danger: unmatchedCount > 0 },
    { key: 'exceptions', label: 'Exceptions',       count: exceptionCount, danger: exceptionCount > 0 },
    { key: 'from_oa',    label: 'OA Invoices' },
  ]

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Invoice Management</h1>
          <p className="mt-1 text-sm text-neutral-500">
            {unmatchedCount > 0
              ? `${unmatchedCount} invoice${unmatchedCount > 1 ? 's' : ''} awaiting PO match`
              : 'All invoices are matched'}
            {exceptionCount > 0 && ` · ${exceptionCount} exception${exceptionCount > 1 ? 's' : ''} require attention`}
          </p>
        </div>
        {canUpload && (
          <Button onClick={() => setShowUpload(true)} className="gap-2">
            <Upload className="h-4 w-4" />
            Upload Invoice
          </Button>
        )}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        {[
          { label: 'Total Invoices',  value: allData?.total ?? 0,                                   color: 'text-neutral-900' },
          { label: 'Unmatched',       value: unmatchedCount,                                        color: unmatchedCount > 0 ? 'text-warning-600' : 'text-neutral-900' },
          { label: 'Exceptions',      value: exceptionCount,                                        color: exceptionCount > 0 ? 'text-danger-600'  : 'text-neutral-900' },
          { label: 'Matched / Ready', value: invoices.filter((i) => i.status === 'matched').length, color: 'text-success-600' },
          { label: 'Paid',            value: invoices.filter((i) => i.status === 'paid').length,    color: 'text-primary-600' },
        ].map((s) => (
          <div key={s.label} className="rounded-xl border border-neutral-200 bg-white p-4">
            <p className="text-xs text-neutral-400">{s.label}</p>
            <p className={cn('text-2xl font-bold mt-1', s.color)}>{s.value}</p>
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-neutral-200">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={cn(
              'flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors',
              activeTab === t.key
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-neutral-500 hover:text-neutral-700'
            )}
          >
            {t.label}
            {t.count !== undefined && t.count > 0 && (
              <span className={cn('inline-flex items-center justify-center rounded-full min-w-[18px] h-[18px] px-1 text-xs font-bold',
                t.danger ? 'bg-danger-100 text-danger-700' : 'bg-neutral-100 text-neutral-600')}>
                {t.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'unmatched'  && <UnmatchedTab />}
      {activeTab === 'exceptions' && <ExceptionsTab />}
      {activeTab === 'all'        && <AllInvoicesTab />}
      {activeTab === 'from_oa'    && <OaInvoicesTab />}

      {/* Upload modal */}
      {showUpload && (
        <UploadModal
          onClose={() => setShowUpload(false)}
          onUploaded={(id, kind) => {
            setShowUpload(false)
            navigate(kind === 'credit' ? '/vendor-credits' : `/invoices/${id}`)
          }}
        />
      )}
    </div>
  )
}
