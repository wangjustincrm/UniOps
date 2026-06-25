import { create } from 'zustand'
import type { ProcurementType, Currency } from '@/types'

export type GrStatus =
  | 'pending_ack'        // Warehouse saved GR, requester hasn't acknowledged
  | 'collection_pending' // Requester acknowledged, physical goods await collection
  | 'collected'          // Requester confirmed collection (physical)
  | 'confirmed'          // Service GR confirmed by requester
  | 'discrepancy'        // Has unresolved discrepancy issues
  | 'cancelled'

export type GrLineCondition = 'good' | 'discrepancy' | 'damaged'

export interface GrLineItem {
  id: string
  poLineId: string
  description: string
  materialId?: string
  qtyOrdered: number
  qtyReceived: number
  unit: string
  unitPrice: number
  lineTotal: number
  condition: GrLineCondition
  discrepancyNotes?: string
  actualQty?: number    // overrides qtyReceived when discrepancy is noted
}

export interface GrRecord {
  id: string
  number: string          // GR-YYYYMMDD-XXXX
  poId: string
  poNumber: string
  prId?: string
  prNumber?: string
  title: string
  vendor: string
  grType: 'physical' | 'service'
  procurementType: ProcurementType
  currency: Currency
  status: GrStatus
  lineItems: GrLineItem[]
  storageLocation: string
  receivedBy: string      // warehouse staff name
  receivedAt: string      // ISO date
  notes?: string
  acknowledgedAt?: string
  acknowledgedBy?: string
  collectedAt?: string
  collectedBy?: string
  collectionNotes?: string   // requester notes recorded at collection/service-confirm
  attachments?: { name: string; size: string }[]
}

let _grSeq = 4

function nextGrNumber(): string {
  _grSeq++
  const now = new Date()
  const yyyymmdd =
    now.getFullYear() +
    String(now.getMonth() + 1).padStart(2, '0') +
    String(now.getDate()).padStart(2, '0')
  return `GR-${yyyymmdd}-${String(_grSeq).padStart(4, '0')}`
}

/** Physical GR types: 1 (Raw Mat), 2 (Consumables), 3 (Spare Parts), 5 (Fixed Asset) */
export function isPhysicalGr(procurementType: ProcurementType): boolean {
  return [1, 2, 3, 5].includes(procurementType)
}

const DEMO_GRS: GrRecord[] = [
  {
    id: 'gr1',
    number: 'GR-20260401-0001',
    poId: 'po1',
    poNumber: 'PO-ABC-2603-01',
    prId: 'pr1',
    prNumber: 'PR-20260315-0001',
    title: 'Office Supplies',
    vendor: 'ABC Supplies Ltd',
    grType: 'physical',
    procurementType: 2,
    currency: 'CAD',
    status: 'collection_pending',
    storageLocation: 'Bay 3A, Technical Warehouse',
    receivedBy: 'Mike Johnson',
    receivedAt: '2026-04-01',
    acknowledgedAt: '2026-04-01T14:00:00.000Z',
    acknowledgedBy: 'Jane Smith',
    lineItems: [
      {
        id: 'gl1',
        poLineId: '1',
        description: 'Office Chair (Ergo Pro)',
        qtyOrdered: 4,
        qtyReceived: 4,
        unit: 'pcs',
        unitPrice: 400,
        lineTotal: 1600,
        condition: 'good',
      },
      {
        id: 'gl2',
        poLineId: '2',
        description: 'Standing Desk',
        qtyOrdered: 2,
        qtyReceived: 2,
        unit: 'pcs',
        unitPrice: 600,
        lineTotal: 1200,
        condition: 'good',
      },
      {
        id: 'gl3',
        poLineId: '3',
        description: 'Monitor Stand',
        qtyOrdered: 6,
        qtyReceived: 4,
        unit: 'pcs',
        unitPrice: 66.67,
        lineTotal: 400.02,
        condition: 'discrepancy',
        discrepancyNotes: 'Only 4 units received, 2 still in transit per vendor.',
        actualQty: 4,
      },
    ],
    notes: 'Items delivered to receiving dock B. Monitor stands incomplete — vendor to ship remaining 2 units.',
  },
  {
    id: 'gr3',
    number: 'GR-20260402-0003',
    poId: 'po5',
    poNumber: 'PO-SAL-2603-01',
    prId: 'pr5',
    prNumber: 'PR-20260317-0009',
    title: 'CRM Software Implementation — Milestone 1',
    vendor: 'Salesforce Canada',
    grType: 'service',
    procurementType: 6,
    currency: 'CAD',
    status: 'pending_ack',
    storageLocation: '—',
    receivedBy: 'Jane Smith',
    receivedAt: '2026-04-02',
    lineItems: [
      {
        id: 'gl5',
        poLineId: '1',
        description: 'Implementation Consulting (Milestone 1)',
        qtyOrdered: 80,
        qtyReceived: 40,
        unit: 'hour',
        unitPrice: 600,
        lineTotal: 24000,
        condition: 'good',
      },
      {
        id: 'gl6',
        poLineId: '2',
        description: 'Training Sessions',
        qtyOrdered: 5,
        qtyReceived: 2,
        unit: 'pcs',
        unitPrice: 2000,
        lineTotal: 4000,
        condition: 'good',
      },
    ],
    notes: 'Milestone 1 delivery: system setup + initial consulting. Training sessions 3–5 scheduled for Q2.',
  },
  {
    id: 'gr2',
    number: 'GR-20260315-0002',
    poId: 'po3',
    poNumber: 'PO-XYZ-2603-01',
    prId: 'pr3',
    prNumber: 'PR-20260301-0007',
    title: 'Cleaning Service',
    vendor: 'XYZ Cleaning Services',
    grType: 'service',
    procurementType: 4,
    currency: 'CAD',
    status: 'confirmed',
    storageLocation: '—',
    receivedBy: 'Jane Smith',
    receivedAt: '2026-03-15',
    acknowledgedAt: '2026-03-15T10:00:00.000Z',
    acknowledgedBy: 'Jane Smith',
    collectedAt: '2026-03-15T10:00:00.000Z',
    collectedBy: 'Jane Smith',
    lineItems: [
      {
        id: 'gl4',
        poLineId: '1',
        description: 'Monthly Cleaning Service',
        qtyOrdered: 1,
        qtyReceived: 1,
        unit: 'month',
        unitPrice: 1800,
        lineTotal: 1800,
        condition: 'good',
      },
    ],
  },
]

interface GrState {
  grs: GrRecord[]
  addGr: (data: Omit<GrRecord, 'id' | 'number'>) => GrRecord
  getGr: (id: string) => GrRecord | undefined
  getGrsByPo: (poId: string) => GrRecord[]
  updateGrStatus: (id: string, status: GrStatus, extra?: Partial<GrRecord>) => void
  updateGr: (id: string, patch: Partial<GrRecord>) => void
}

export const useGrStore = create<GrState>()((set, get) => ({
  grs: DEMO_GRS,

  addGr: (data) => {
    const newGr: GrRecord = {
      ...data,
      id: crypto.randomUUID(),
      number: nextGrNumber(),
    }
    set((s) => ({ grs: [newGr, ...s.grs] }))
    return newGr
  },

  getGr: (id) => get().grs.find((g) => g.id === id),

  getGrsByPo: (poId) => get().grs.filter((g) => g.poId === poId),

  updateGrStatus: (id, status, extra = {}) =>
    set((s) => ({
      grs: s.grs.map((g) => (g.id === id ? { ...g, status, ...extra } : g)),
    })),

  updateGr: (id, patch) =>
    set((s) => ({
      grs: s.grs.map((g) => (g.id === id ? { ...g, ...patch } : g)),
    })),
}))
