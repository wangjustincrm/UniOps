import { create } from 'zustand'
import type { DocumentStatus, ProcurementType, PrLineItem, Currency } from '@/types'

export interface PrRecord {
  id: string
  number: string
  title: string
  vendor: string
  vendorPoid: string
  amount: number
  currency: Currency
  type: ProcurementType
  costCenterCode: string  // CostCenter.code
  budgetCode: string
  status: DocumentStatus
  submittedAt: string
  requiredBy: string
  deliveryAddress?: string
  lineItems: PrLineItem[]
  notes?: string
  poId?: string
  poNumber?: string
  pdfGeneratedAt?: string   // set when PDF is auto-generated on approval
  approvalStepIdx?: number
}

const DEMO: PrRecord[] = [
  {
    id: 'pr1',
    number: 'PR-20260315-0001',
    title: 'Office Supplies',
    vendor: 'ABC Supplies Ltd',
    vendorPoid: 'ABC',
    amount: 3200,
    currency: 'CAD',
    type: 2,
    costCenterCode: 'CC-MKT-02',
    budgetCode: 'CRM003-01',
    status: 'approved',
    submittedAt: '2026-03-15',
    requiredBy: '2026-03-30',
    poId: 'po1',
    poNumber: 'PO-ABC-2603-01',
    pdfGeneratedAt: '2026-03-15T14:30:00.000Z',
    lineItems: [
      { id: '1', description: 'Office Chair (Ergo Pro)', qty: 4, unit: 'pcs', unitPrice: 400, lineTotal: 1600 },
      { id: '2', description: 'Standing Desk', qty: 2, unit: 'pcs', unitPrice: 600, lineTotal: 1200 },
      { id: '3', description: 'Monitor Stand', qty: 6, unit: 'pcs', unitPrice: 400/6, lineTotal: 400 },
    ],
  },
  {
    id: 'pr2',
    number: 'PR-20260310-0003',
    title: 'IT Laptop — Dell XPS 15',
    vendor: 'Dell Canada',
    vendorPoid: 'DEL',
    amount: 2400,
    currency: 'USD',
    type: 5,
    costCenterCode: 'CC-ITS-01',
    budgetCode: 'IT001-03',
    status: 'in_review',
    submittedAt: '2026-03-10',
    requiredBy: '2026-03-25',
    lineItems: [
      { id: '1', description: 'Dell XPS 15 Laptop', qty: 1, unit: 'pcs', unitPrice: 2400, lineTotal: 2400 },
    ],
  },
  {
    id: 'pr3',
    number: 'PR-20260301-0007',
    title: 'Cleaning Service',
    vendor: 'XYZ Cleaning Services',
    vendorPoid: 'XYZ',
    amount: 1800,
    currency: 'CAD',
    type: 4,
    costCenterCode: 'CC-HRS-01',
    budgetCode: 'HR001-02',
    status: 'paid',
    submittedAt: '2026-03-01',
    requiredBy: '2026-03-15',
    pdfGeneratedAt: '2026-03-01T10:00:00.000Z',
    lineItems: [
      { id: '1', description: 'Monthly Cleaning Service', qty: 1, unit: 'month', unitPrice: 1800, lineTotal: 1800 },
    ],
  },
  {
    id: 'pr4',
    number: 'PR-20260318-0002',
    title: 'Raw Materials Q2',
    vendor: 'PQR Materials Inc',
    vendorPoid: 'PQR',
    amount: 145000,
    currency: 'RMB',
    type: 1,
    costCenterCode: '—',
    budgetCode: '—',
    status: 'submitted',
    submittedAt: '2026-03-18',
    requiredBy: '2026-04-10',
    lineItems: [
      { id: '1', description: 'Steel Sheet 2mm', materialId: 'MAT-001', qty: 500, unit: 'kg', unitPrice: 180, lineTotal: 90000 },
      { id: '2', description: 'Aluminium Profile', materialId: 'MAT-002', qty: 200, unit: 'kg', unitPrice: 275, lineTotal: 55000 },
    ],
  },
  {
    id: 'pr5',
    number: 'PR-20260317-0009',
    title: 'CRM Software Implementation',
    vendor: 'Salesforce Canada',
    vendorPoid: 'SAL',
    amount: 58000,
    currency: 'CAD',
    type: 6,
    costCenterCode: 'CC-MKT-02',
    budgetCode: 'CRM003-02',
    status: 'draft',
    submittedAt: '2026-03-17',
    requiredBy: '2026-04-30',
    lineItems: [
      { id: '1', description: 'Implementation Consulting', qty: 80, unit: 'hour', unitPrice: 600, lineTotal: 48000 },
      { id: '2', description: 'Training Sessions', qty: 5, unit: 'pcs', unitPrice: 2000, lineTotal: 10000 },
    ],
  },
  {
    id: 'pr6',
    number: 'PR-20260316-0011',
    title: 'Safety Equipment',
    vendor: '—',
    vendorPoid: '',
    amount: 4200,
    currency: 'EUR',
    type: 3,
    costCenterCode: 'CC-HRS-01',
    budgetCode: 'HR001-01',
    status: 'returned',
    submittedAt: '2026-03-16',
    requiredBy: '2026-03-28',
    lineItems: [
      { id: '1', description: 'Hard Hat', materialId: 'MAT-100', qty: 20, unit: 'pcs', unitPrice: 45, lineTotal: 900 },
      { id: '2', description: 'Safety Vest', materialId: 'MAT-101', qty: 20, unit: 'pcs', unitPrice: 30, lineTotal: 600 },
      { id: '3', description: 'Steel-Toe Boots', materialId: 'MAT-102', qty: 15, unit: 'pair', unitPrice: 180, lineTotal: 2700 },
    ],
  },
]

let _seqByDate: Record<string, number> = {}
function nextPrNumber(): string {
  const today = new Date()
  const dateStr = [
    today.getFullYear(),
    String(today.getMonth() + 1).padStart(2, '0'),
    String(today.getDate()).padStart(2, '0'),
  ].join('')
  _seqByDate[dateStr] = (_seqByDate[dateStr] ?? 0) + 1
  return `PR-${dateStr}-${String(_seqByDate[dateStr]).padStart(4, '0')}`
}

interface PrState {
  prs: PrRecord[]
  addPr: (pr: Omit<PrRecord, 'id' | 'number' | 'status' | 'submittedAt'>) => PrRecord
  getPr: (id: string) => PrRecord | undefined
  updatePrStatus: (id: string, status: DocumentStatus) => void
  setPdfGenerated: (id: string) => void
  linkPo: (prId: string, poId: string, poNumber: string) => void
  setPrApprovalStep: (id: string, stepIdx: number, status: DocumentStatus) => void
}

export const usePrStore = create<PrState>()((set, get) => ({
  prs: DEMO,

  addPr: (data) => {
    const newPr: PrRecord = {
      ...data,
      id: crypto.randomUUID(),
      number: nextPrNumber(),
      status: 'submitted',
      submittedAt: new Date().toISOString().slice(0, 10),
    }
    set((s) => ({ prs: [newPr, ...s.prs] }))
    return newPr
  },

  getPr: (id) => get().prs.find((p) => p.id === id),

  updatePrStatus: (id, status) =>
    set((s) => ({ prs: s.prs.map((p) => (p.id === id ? { ...p, status } : p)) })),

  setPdfGenerated: (id) =>
    set((s) => ({
      prs: s.prs.map((p) =>
        p.id === id ? { ...p, pdfGeneratedAt: new Date().toISOString() } : p
      ),
    })),

  linkPo: (prId, poId, poNumber) =>
    set((s) => ({ prs: s.prs.map((p) => (p.id === prId ? { ...p, poId, poNumber } : p)) })),

  setPrApprovalStep: (id, stepIdx, status) =>
    set((s) => ({ prs: s.prs.map((p) => p.id === id ? { ...p, approvalStepIdx: stepIdx, status } : p) })),
}))
