import { create } from 'zustand'
import type { ProcurementType, PrLineItem, Currency } from '@/types'

export type PoStatus =
  | 'draft'
  | 'submitted'
  | 'in_review'
  | 'approved'
  | 'issued'
  | 'closed'
  | 'cancelled'

export interface PoLineItem extends PrLineItem {
  receivedQty: number
}

export interface PoRecord {
  id: string
  number: string
  prId?: string
  prNumber?: string
  title: string
  vendor: string
  vendorPoid: string
  budgetCode: string
  type: ProcurementType
  lineItems: PoLineItem[]
  subtotal: number
  taxRate: number       // 0 | 0.05 | 0.13 | 0.15
  taxAmount: number
  total: number
  currency: Currency
  status: PoStatus
  createdAt: string
  expectedDelivery: string
  deliveryAddress?: string
  notes?: string
  attachments?: { name: string; size: string }[]
  pdfGeneratedAt?: string   // set when PDF is auto-generated on approval
  approvalStepIdx?: number
}

let _seq: Record<string, number> = {}

function nextPoNumber(vendorPoid: string): string {
  const now = new Date()
  const yymm =
    String(now.getFullYear()).slice(2) +
    String(now.getMonth() + 1).padStart(2, '0')
  const key = `${vendorPoid || 'GEN'}-${yymm}`
  _seq[key] = (_seq[key] ?? 0) + 1
  return `PO-${vendorPoid || 'GEN'}-${yymm}-${String(_seq[key]).padStart(2, '0')}`
}

function li(
  id: string,
  desc: string,
  qty: number,
  unit: string,
  unitPrice: number,
  materialId?: string
): PoLineItem {
  return {
    id,
    description: desc,
    materialId: materialId ?? '',
    supplierItemId: '',
    qty,
    unit,
    unitPrice,
    lineTotal: Math.round(qty * unitPrice * 100) / 100,
    notes: '',
    receivedQty: 0,
  }
}

const DEMO_POS: PoRecord[] = [
  {
    id: 'po1',
    number: 'PO-ABC-2603-01',
    prId: 'pr1',
    prNumber: 'PR-20260315-0001',
    title: 'Office Supplies',
    vendor: 'ABC Supplies Ltd',
    vendorPoid: 'ABC',
    budgetCode: 'CRM003-01',
    type: 2,
    lineItems: [
      li('1', 'Office Chair (Ergo Pro)', 4, 'pcs', 400),
      li('2', 'Standing Desk', 2, 'pcs', 600),
      li('3', 'Monitor Stand', 6, 'pcs', 66.67),
    ],
    subtotal: 3200,
    taxRate: 0.13,
    taxAmount: 416,
    total: 3616,
    currency: 'CAD' as Currency,
    status: 'issued',
    createdAt: '2026-03-16',
    expectedDelivery: '2026-03-30',
    deliveryAddress: 'Technical Warehouse, Building A',
    notes: 'Please deliver to receiving dock B.',
    pdfGeneratedAt: '2026-03-16T09:00:00.000Z',
  },
  {
    id: 'po2',
    number: 'PO-DEL-2603-01',
    prId: 'pr2',
    prNumber: 'PR-20260310-0003',
    title: 'IT Laptop — Dell XPS 15',
    vendor: 'Dell Canada',
    vendorPoid: 'DEL',
    budgetCode: 'IT001-03',
    type: 5,
    lineItems: [li('1', 'Dell XPS 15 Laptop', 1, 'pcs', 2400)],
    subtotal: 2400,
    taxRate: 0,
    taxAmount: 0,
    total: 2400,
    currency: 'USD',
    status: 'in_review',
    createdAt: '2026-03-11',
    expectedDelivery: '2026-03-25',
    deliveryAddress: 'IT Dept, Floor 3',
  },
  {
    id: 'po3',
    number: 'PO-XYZ-2603-01',
    prId: 'pr3',
    prNumber: 'PR-20260301-0007',
    title: 'Cleaning Service',
    vendor: 'XYZ Cleaning Services',
    vendorPoid: 'XYZ',
    budgetCode: 'HR001-02',
    type: 4,
    lineItems: [li('1', 'Monthly Cleaning Service', 1, 'month', 1800)],
    subtotal: 1800,
    taxRate: 0.05,
    taxAmount: 90,
    total: 1890,
    currency: 'CAD' as Currency,
    status: 'closed',
    createdAt: '2026-03-02',
    expectedDelivery: '2026-03-15',
    pdfGeneratedAt: '2026-03-02T10:00:00.000Z',
  },
  {
    id: 'po4',
    number: 'PO-PQR-2603-01',
    prId: 'pr4',
    prNumber: 'PR-20260318-0002',
    title: 'Raw Materials Q2',
    vendor: 'PQR Materials Inc',
    vendorPoid: 'PQR',
    budgetCode: '—',
    type: 1,
    lineItems: [
      li('1', 'Steel Sheet 2mm', 500, 'kg', 180, 'MAT-001'),
      li('2', 'Aluminium Profile', 200, 'kg', 275, 'MAT-002'),
    ],
    subtotal: 145000,
    taxRate: 0,
    taxAmount: 0,
    total: 145000,
    currency: 'RMB',
    status: 'draft',
    createdAt: '2026-03-18',
    expectedDelivery: '2026-04-15',
    deliveryAddress: 'Production Floor, Bay 2',
  },
  {
    id: 'po5',
    number: 'PO-SAL-2603-01',
    prId: 'pr5',
    prNumber: 'PR-20260317-0009',
    title: 'CRM Software Implementation',
    vendor: 'Salesforce Canada',
    vendorPoid: 'SAL',
    budgetCode: 'CRM003-02',
    type: 6,
    lineItems: [
      li('1', 'Implementation Consulting', 80, 'hour', 600),
      li('2', 'Training Sessions', 5, 'pcs', 2000),
    ],
    subtotal: 58000,
    taxRate: 0.13,
    taxAmount: 7540,
    total: 65540,
    currency: 'CAD' as Currency,
    status: 'approved',
    createdAt: '2026-03-17',
    expectedDelivery: '2026-04-30',
    notes: 'Milestone-based delivery schedule attached.',
    pdfGeneratedAt: '2026-03-17T11:30:00.000Z',
  },
]

interface PoState {
  pos: PoRecord[]
  addPo: (data: Omit<PoRecord, 'id' | 'number' | 'createdAt'>) => PoRecord
  getPo: (id: string) => PoRecord | undefined
  updatePoStatus: (id: string, status: PoStatus) => void
  updatePo: (id: string, patch: Partial<PoRecord>) => void
  setPdfGenerated: (id: string) => void
  setPoApprovalStep: (id: string, stepIdx: number, status: PoStatus) => void
}

export const usePoStore = create<PoState>()((set, get) => ({
  pos: DEMO_POS,

  addPo: (data) => {
    const newPo: PoRecord = {
      ...data,
      id: crypto.randomUUID(),
      number: nextPoNumber(data.vendorPoid),
      createdAt: new Date().toISOString().slice(0, 10),
    }
    set((s) => ({ pos: [newPo, ...s.pos] }))
    return newPo
  },

  getPo: (id) => get().pos.find((p) => p.id === id),

  updatePoStatus: (id, status) =>
    set((s) => ({ pos: s.pos.map((p) => (p.id === id ? { ...p, status } : p)) })),

  updatePo: (id, patch) =>
    set((s) => ({ pos: s.pos.map((p) => (p.id === id ? { ...p, ...patch } : p)) })),

  setPdfGenerated: (id) =>
    set((s) => ({
      pos: s.pos.map((p) =>
        p.id === id ? { ...p, pdfGeneratedAt: new Date().toISOString() } : p
      ),
    })),

  setPoApprovalStep: (id, stepIdx, status) =>
    set((s) => ({ pos: s.pos.map((p) => p.id === id ? { ...p, approvalStepIdx: stepIdx, status } : p) })),
}))
