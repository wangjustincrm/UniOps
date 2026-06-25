import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface CostCenter {
  id: string
  code: string         // e.g. "CC-MKT-01"
  name: string         // e.g. "Brand & Campaigns"
  departmentId: string // references DepartmentRecord.id
  isActive: boolean
}

const SEED_COST_CENTERS: CostCenter[] = [
  { id: 'cc1', code: 'CC-MKT-01', name: 'Brand & Campaigns', departmentId: 'd1', isActive: true },
  { id: 'cc2', code: 'CC-MKT-02', name: 'CRM & Digital',     departmentId: 'd1', isActive: true },
  { id: 'cc3', code: 'CC-ITS-01', name: 'Infrastructure',    departmentId: 'd6', isActive: true },
  { id: 'cc4', code: 'CC-ITS-02', name: 'Software & SaaS',   departmentId: 'd6', isActive: true },
  { id: 'cc5', code: 'CC-HRS-01', name: 'Talent & Staffing', departmentId: 'd7', isActive: true },
  { id: 'cc6', code: 'CC-FIN-01', name: 'Finance Operations', departmentId: 'd2', isActive: true },
  { id: 'cc7', code: 'CC-OPS-01', name: 'Warehouse & Logistics', departmentId: 'd4', isActive: true },
  { id: 'cc8', code: 'CC-PRO-01', name: 'Vendor Management', departmentId: 'd3', isActive: true },
]

interface CostCenterStoreState {
  costCenters: CostCenter[]
  addCostCenter: (data: Omit<CostCenter, 'id'>) => CostCenter
  updateCostCenter: (id: string, patch: Partial<Omit<CostCenter, 'id'>>) => void
  deleteCostCenter: (id: string) => void
}

export const useCostCenterStore = create<CostCenterStoreState>()(
  persist(
    (set) => ({
      costCenters: SEED_COST_CENTERS,

      addCostCenter: (data) => {
        const cc: CostCenter = { ...data, id: crypto.randomUUID() }
        set((s) => ({ costCenters: [...s.costCenters, cc] }))
        return cc
      },

      updateCostCenter: (id, patch) =>
        set((s) => ({
          costCenters: s.costCenters.map((c) => (c.id === id ? { ...c, ...patch } : c)),
        })),

      deleteCostCenter: (id) =>
        set((s) => ({ costCenters: s.costCenters.filter((c) => c.id !== id) })),
    }),
    { name: 'epms-cost-centers' }
  )
)
