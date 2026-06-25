import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface DepartmentRecord {
  id: string
  name: string
  code: string        // short code, e.g. "MKT" — used in budget codes, reports
  description: string
  isActive: boolean
  createdAt: string
}

const INITIAL_DEPARTMENTS: DepartmentRecord[] = [
  { id: 'd1', name: 'Marketing',   code: 'MKT', description: 'Brand, campaigns and CRM',         isActive: true, createdAt: '2026-01-01' },
  { id: 'd2', name: 'Finance',     code: 'FIN', description: 'Accounting, AP/AR, treasury',       isActive: true, createdAt: '2026-01-01' },
  { id: 'd3', name: 'Procurement', code: 'PRO', description: 'Purchasing and vendor management',  isActive: true, createdAt: '2026-01-01' },
  { id: 'd4', name: 'Operations',  code: 'OPS', description: 'Warehouse, logistics and delivery', isActive: true, createdAt: '2026-01-01' },
  { id: 'd5', name: 'Executive',   code: 'EXE', description: 'Senior leadership team',            isActive: true, createdAt: '2026-01-01' },
  { id: 'd6', name: 'IT',          code: 'ITS', description: 'Infrastructure and systems',        isActive: true, createdAt: '2026-01-01' },
  { id: 'd7', name: 'HR',          code: 'HRS', description: 'Human resources and talent',        isActive: true, createdAt: '2026-01-01' },
]

interface DepartmentStoreState {
  departments: DepartmentRecord[]
  addDepartment: (data: Omit<DepartmentRecord, 'id' | 'createdAt'>) => DepartmentRecord
  updateDepartment: (id: string, patch: Partial<Omit<DepartmentRecord, 'id' | 'createdAt'>>) => void
  deleteDepartment: (id: string) => void
}

export const useDepartmentStore = create<DepartmentStoreState>()(
  persist(
    (set) => ({
      departments: INITIAL_DEPARTMENTS,

      addDepartment: (data) => {
        const newDept: DepartmentRecord = {
          ...data,
          id: crypto.randomUUID(),
          createdAt: new Date().toISOString().slice(0, 10),
        }
        set((s) => ({ departments: [...s.departments, newDept] }))
        return newDept
      },

      updateDepartment: (id, patch) =>
        set((s) => ({
          departments: s.departments.map((d) => (d.id === id ? { ...d, ...patch } : d)),
        })),

      deleteDepartment: (id) =>
        set((s) => ({ departments: s.departments.filter((d) => d.id !== id) })),
    }),
    { name: 'epms-departments' }
  )
)
