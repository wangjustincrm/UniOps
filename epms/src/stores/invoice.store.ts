import { create } from 'zustand'
import type { Currency } from '@/types'

export type InvoiceStatus =
  | 'unmatched'     // uploaded, not yet linked to a PO
  | 'matched'       // linked to PO, 3-way match within tolerance
  | 'exception'     // linked to PO but variance exceeds threshold
  | 'match_review'  // matched with variance, awaiting AP/Finance review
  | 'approved'      // approved for payment application
  | 'paid'          // paid

export interface InvoiceRecord {
  id: string
  internalRef: string          // system ref: INV-YYYY-XXXX
  vendorInvoiceNumber: string  // number printed on vendor's invoice
  vendor: string
  vendorPoid?: string
  amount: number               // pre-tax
  taxAmount: number
  totalAmount: number          // what we pay = amount + tax
  currency: Currency
  invoiceDate: string          // date on the invoice
  dueDate: string
  uploadedAt: string           // ISO datetime
  uploadedBy: string
  status: InvoiceStatus
  fileName?: string
  fileSize?: string
  notes?: string
  // ── Link ──────────────────────────────────────────
  poId?: string
  poNumber?: string
  grId?: string
  grNumber?: string
  // ── 3-way match ───────────────────────────────────
  matchedAt?: string
  matchedBy?: string
  poTotal?: number             // PO total at time of matching
  grValue?: number             // GR received value at time of matching
  variance?: number            // totalAmount − poTotal (signed)
  variancePct?: number         // variance / poTotal × 100
  // ── Exception ─────────────────────────────────────
  exceptionReason?: string
  exceptionResolvedAt?: string
  exceptionResolvedBy?: string
  exceptionResolution?: string // 'accepted' | 'credit_note_requested'
}

// ─── SLA helper (2 business-day threshold for unmatched invoices) ────────────

export function computeSla(uploadedAt: string): { days: number; status: 'on_time' | 'warning' | 'overdue' } {
  const diffMs = Date.now() - new Date(uploadedAt).getTime()
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24))
  return {
    days,
    status: days >= 2 ? 'overdue' : days === 1 ? 'warning' : 'on_time',
  }
}

// ─── Sequence counter ────────────────────────────────────────────────────────

let _invSeq = 4

function nextInternalRef(): string {
  _invSeq++
  const year = new Date().getFullYear()
  return `INV-${year}-${String(_invSeq).padStart(3, '0')}`
}

// ─── Demo data ────────────────────────────────────────────────────────────────

// Dates relative to today for realistic SLA display
const daysAgo = (n: number) => {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString()
}
const dateStr = (n: number) => {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

const DEMO_INVOICES: InvoiceRecord[] = [
  {
    // ── Unmatched — SLA overdue (3 days old) ─────────────────────────────────
    id: 'inv1',
    internalRef: 'INV-2026-001',
    vendorInvoiceNumber: 'ABC-2026-0821',
    vendor: 'ABC Supplies Ltd',
    vendorPoid: 'ABC',
    amount: 3200,
    taxAmount: 416,
    totalAmount: 3616,
    currency: 'CAD',
    invoiceDate: dateStr(5),
    dueDate: dateStr(-25),      // due in 25 days
    uploadedAt: daysAgo(3),
    uploadedBy: 'Sarah Lee',
    status: 'unmatched',
    fileName: 'ABC_Invoice_0821.pdf',
    fileSize: '420 KB',
    notes: 'Invoice for March office supplies order.',
  },
  {
    // ── Unmatched — SLA warning (1 day old) ──────────────────────────────────
    id: 'inv2',
    internalRef: 'INV-2026-002',
    vendorInvoiceNumber: 'DEL-CA-9142',
    vendor: 'Dell Canada',
    vendorPoid: 'DEL',
    amount: 2400,
    taxAmount: 0,
    totalAmount: 2400,
    currency: 'USD',
    invoiceDate: dateStr(3),
    dueDate: dateStr(-27),
    uploadedAt: daysAgo(1),
    uploadedBy: 'Sarah Lee',
    status: 'unmatched',
    fileName: 'Dell_Invoice_9142.pdf',
    fileSize: '285 KB',
  },
  {
    // ── Matched — no exception ────────────────────────────────────────────────
    id: 'inv3',
    internalRef: 'INV-2026-003',
    vendorInvoiceNumber: 'XYZ-2026-0318',
    vendor: 'XYZ Cleaning Services',
    vendorPoid: 'XYZ',
    amount: 1800,
    taxAmount: 90,
    totalAmount: 1890,
    currency: 'CAD',
    invoiceDate: '2026-03-15',
    dueDate: '2026-04-14',
    uploadedAt: '2026-03-16T09:00:00.000Z',
    uploadedBy: 'Sarah Lee',
    status: 'matched',
    fileName: 'XYZ_Invoice_0318.pdf',
    fileSize: '198 KB',
    poId: 'po3',
    poNumber: 'PO-XYZ-2603-01',
    grId: 'gr2',
    grNumber: 'GR-20260315-0002',
    matchedAt: '2026-03-16T11:00:00.000Z',
    matchedBy: 'Sarah Lee',
    poTotal: 1890,
    grValue: 1890,
    variance: 0,
    variancePct: 0,
  },
  {
    // ── Exception — invoice over PO amount ───────────────────────────────────
    id: 'inv4',
    internalRef: 'INV-2026-004',
    vendorInvoiceNumber: 'SAL-CA-INV-2603',
    vendor: 'Salesforce Canada',
    vendorPoid: 'SAL',
    amount: 60353.98,
    taxAmount: 7846.02,
    totalAmount: 68200,
    currency: 'CAD',
    invoiceDate: '2026-03-28',
    dueDate: '2026-04-27',
    uploadedAt: '2026-03-29T14:00:00.000Z',
    uploadedBy: 'Sarah Lee',
    status: 'exception',
    fileName: 'Salesforce_Invoice_2603.pdf',
    fileSize: '612 KB',
    poId: 'po5',
    poNumber: 'PO-SAL-2603-01',
    matchedAt: '2026-03-29T15:00:00.000Z',
    matchedBy: 'Sarah Lee',
    poTotal: 65540,
    grValue: 28000,  // partial delivery (gr3 milestone 1 only)
    variance: 2660,
    variancePct: 4.06,
    exceptionReason: 'Invoice amount (CAD $68,200) exceeds PO total (CAD $65,540) by CAD $2,660 (4.1%). Possible additional charges not in PO scope.',
  },
]

// ─── Store ────────────────────────────────────────────────────────────────────

interface InvoiceState {
  invoices: InvoiceRecord[]
  addInvoice: (data: Omit<InvoiceRecord, 'id' | 'internalRef'>) => InvoiceRecord
  getInvoice: (id: string) => InvoiceRecord | undefined
  matchInvoice: (id: string, poId: string, poNumber: string, poTotal: number, grId: string | undefined, grNumber: string | undefined, grValue: number | undefined, matchedBy: string) => void
  resolveException: (id: string, resolution: 'accepted' | 'credit_note_requested', note: string, resolvedBy: string) => void
  updateStatus: (id: string, status: InvoiceStatus) => void
}

export const useInvoiceStore = create<InvoiceState>()((set, get) => ({
  invoices: DEMO_INVOICES,

  addInvoice: (data) => {
    const inv: InvoiceRecord = { ...data, id: crypto.randomUUID(), internalRef: nextInternalRef() }
    set((s) => ({ invoices: [inv, ...s.invoices] }))
    return inv
  },

  getInvoice: (id) => get().invoices.find((i) => i.id === id),

  matchInvoice: (id, poId, poNumber, poTotal, grId, grNumber, grValue, matchedBy) => {
    const inv = get().invoices.find((i) => i.id === id)
    if (!inv) return
    const variance = inv.totalAmount - poTotal
    const variancePct = poTotal > 0 ? (variance / poTotal) * 100 : 0
    const isException = Math.abs(variancePct) > 5

    set((s) => ({
      invoices: s.invoices.map((i) =>
        i.id === id
          ? {
              ...i,
              poId, poNumber, grId, grNumber,
              matchedAt: new Date().toISOString(),
              matchedBy,
              poTotal,
              grValue,
              variance,
              variancePct: Math.round(variancePct * 100) / 100,
              status: isException ? 'exception' : 'matched',
              exceptionReason: isException
                ? `Invoice amount (${i.currency} ${i.totalAmount.toLocaleString()}) differs from PO total (${i.currency} ${poTotal.toLocaleString()}) by ${i.currency} ${Math.abs(variance).toLocaleString()} (${Math.abs(variancePct).toFixed(1)}%).`
                : undefined,
            }
          : i
      ),
    }))
  },

  resolveException: (id, resolution, note, resolvedBy) => {
    set((s) => ({
      invoices: s.invoices.map((i) =>
        i.id === id
          ? {
              ...i,
              status: resolution === 'accepted' ? 'matched' : 'exception',
              exceptionResolvedAt: new Date().toISOString(),
              exceptionResolvedBy: resolvedBy,
              exceptionResolution: resolution,
              notes: note,
            }
          : i
      ),
    }))
  },

  updateStatus: (id, status) =>
    set((s) => ({ invoices: s.invoices.map((i) => (i.id === id ? { ...i, status } : i)) })),
}))
