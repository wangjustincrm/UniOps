import { create } from 'zustand'
import type { Currency, PaLineItem } from '@/types'

// ─── Types ────────────────────────────────────────────────────────────────────

export type PaStatus =
  | 'draft'
  | 'submitted'
  | 'in_review'
  | 'approved'
  | 'processed'   // payment sent / PA closed
  | 'cancelled'

export type PaType =
  | 'regular'     // standard: invoice matched + GR complete
  | 'prepayment'  // advance payment before GR, requires settlement later

export interface PaRecord {
  id: string
  paNumber: string          // PA-YYYYMMDD-XXXX
  poId: string
  poNumber: string
  invoiceIds: string[]      // IDs from invoice store (0..many)
  grIds: string[]           // IDs from gr store (0..many)
  vendor: string
  title: string
  type: PaType
  // Charge breakdown
  subtotal: number          // pre-tax amount (required)
  taxAmount: number         // tax (required, can be 0)
  shippingAmount?: number   // optional
  otherCharges?: number     // optional
  otherChargesNote?: string // description for other charges
  paymentAmount: number     // computed: subtotal + taxAmount + (shipping ?? 0) + (other ?? 0)
  paLineItems: PaLineItem[] // PO line items this PA is paying for
  currency: Currency
  status: PaStatus
  createdAt: string
  createdBy: string
  submittedAt?: string
  notes?: string
  // Prepayment fields
  prepaymentPct?: number            // % of PO total (e.g. 50)
  expectedSettlementDate?: string   // when final invoice expected
  // Settlement (prepayment PA only)
  settlementStatus?: 'pending' | 'settled' | 'disputed'
  settledAt?: string
  settledBy?: string
  settlementNote?: string
  settlementVariance?: number       // final invoice amount − prepayment
  // Approval
  reviewedAt?: string
  reviewedBy?: string
  approvedAt?: string
  approvedBy?: string
  processedAt?: string
  processedBy?: string
  rejectionReason?: string
  approvalStepIdx?: number
}

// ─── Sequence counter ─────────────────────────────────────────────────────────

let _paSeq = 3

function nextPaNumber(): string {
  _paSeq++
  const today = new Date()
  const yyyymmdd = `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, '0')}${String(today.getDate()).padStart(2, '0')}`
  return `PA-${yyyymmdd}-${String(_paSeq).padStart(4, '0')}`
}

// ─── Demo data ────────────────────────────────────────────────────────────────

const DEMO_PAS: PaRecord[] = [
  {
    // ── Processed / Paid regular PA ───────────────────────────────────────────
    id: 'pa1',
    paNumber: 'PA-20260317-0001',
    poId: 'po3',
    poNumber: 'PO-XYZ-2603-01',
    invoiceIds: ['inv3'],
    grIds: ['gr2'],
    vendor: 'XYZ Cleaning Services',
    title: 'Monthly Cleaning Services — March 2026',
    type: 'regular',
    subtotal: 1800,
    taxAmount: 90,
    paymentAmount: 1890,
    paLineItems: [],
    currency: 'CAD',
    status: 'processed',
    createdAt: '2026-03-17T09:00:00.000Z',
    createdBy: 'Sarah Lee',
    submittedAt: '2026-03-17T09:30:00.000Z',
    reviewedAt: '2026-03-18T10:00:00.000Z',
    reviewedBy: 'Finance BP',
    approvedAt: '2026-03-18T14:00:00.000Z',
    approvedBy: 'Michael Chen',
    processedAt: '2026-03-19T09:00:00.000Z',
    processedBy: 'Sarah Lee',
    notes: 'Monthly cleaning service confirmed by requester. GR acknowledged and collected.',
  },
  {
    // ── Prepayment PA — awaiting Finance approval ──────────────────────────────
    id: 'pa2',
    paNumber: 'PA-20260330-0002',
    poId: 'po5',
    poNumber: 'PO-SAL-2603-01',
    invoiceIds: ['inv4'],
    grIds: [],
    vendor: 'Salesforce Canada',
    title: 'CRM Platform Subscription — Prepayment (50%)',
    type: 'prepayment',
    subtotal: 30000,
    taxAmount: 2770,
    paymentAmount: 32770,   // 50% of PO total $65,540
    paLineItems: [],
    currency: 'CAD',
    status: 'in_review',
    createdAt: '2026-03-30T10:00:00.000Z',
    createdBy: 'Sarah Lee',
    submittedAt: '2026-03-30T10:30:00.000Z',
    reviewedAt: '2026-03-31T09:00:00.000Z',
    reviewedBy: 'Finance BP',
    prepaymentPct: 50,
    expectedSettlementDate: '2026-06-30',
    settlementStatus: 'pending',
    notes: 'Prepayment per vendor contract terms. Full delivery and final invoice expected by Q2 end.',
  },
  {
    // ── Draft regular PA — not yet submitted ──────────────────────────────────
    id: 'pa3',
    paNumber: 'PA-20260416-0003',
    poId: 'po3',
    poNumber: 'PO-XYZ-2603-01',
    invoiceIds: [],
    grIds: [],
    vendor: 'XYZ Cleaning Services',
    title: 'Monthly Cleaning Services — April 2026',
    type: 'regular',
    subtotal: 1800,
    taxAmount: 90,
    paymentAmount: 1890,
    paLineItems: [],
    currency: 'CAD',
    status: 'draft',
    createdAt: '2026-04-16T09:00:00.000Z',
    createdBy: 'Sarah Lee',
    notes: 'April monthly service. Pending invoice upload.',
  },
]

// ─── Store ────────────────────────────────────────────────────────────────────

interface PaState {
  pas: PaRecord[]
  addPa: (data: Omit<PaRecord, 'id' | 'paNumber'>) => PaRecord
  getPa: (id: string) => PaRecord | undefined
  updateStatus: (id: string, status: PaStatus, extra?: Partial<PaRecord>) => void
  settlePrepayment: (id: string, note: string, variance: number, settledBy: string) => void
  setPaApprovalStep: (id: string, stepIdx: number, status: PaStatus) => void
}

export const usePaStore = create<PaState>()((set, get) => ({
  pas: DEMO_PAS,

  addPa: (data) => {
    const pa: PaRecord = { ...data, id: crypto.randomUUID(), paNumber: nextPaNumber() }
    set((s) => ({ pas: [pa, ...s.pas] }))
    return pa
  },

  getPa: (id) => get().pas.find((p) => p.id === id),

  updateStatus: (id, status, extra = {}) =>
    set((s) => ({
      pas: s.pas.map((p) => (p.id === id ? { ...p, status, ...extra } : p)),
    })),

  settlePrepayment: (id, note, variance, settledBy) =>
    set((s) => ({
      pas: s.pas.map((p) =>
        p.id === id
          ? {
              ...p,
              settlementStatus: Math.abs(variance) < 0.01 ? 'settled' : 'disputed',
              settledAt: new Date().toISOString(),
              settledBy,
              settlementNote: note,
              settlementVariance: variance,
            }
          : p
      ),
    })),

  setPaApprovalStep: (id, stepIdx, status) =>
    set((s) => ({ pas: s.pas.map((p) => p.id === id ? { ...p, approvalStepIdx: stepIdx, status } : p) })),
}))
