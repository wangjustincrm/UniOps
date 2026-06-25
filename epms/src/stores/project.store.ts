import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface Project {
  id: string
  code: string        // e.g. "PROJ-2026-001"
  name: string
  description: string
  status: 'active' | 'on_hold' | 'closed'
  budget: number
  currency: string
  startDate: string
  endDate: string
  ownerDept: string
  managerName: string
  createdAt: string
}

const DEMO_PROJECTS: Project[] = [
  {
    id: 'prj01',
    code: 'PROJ-2026-001',
    name: 'ERP System Upgrade',
    description:
      'Full upgrade of the enterprise resource planning system from legacy platform to cloud-based solution, including data migration and staff training.',
    status: 'active',
    budget: 380000,
    currency: 'CAD',
    startDate: '2026-01-15',
    endDate: '2026-09-30',
    ownerDept: 'Information Technology',
    managerName: 'Rajesh Gupta',
    createdAt: '2026-01-10',
  },
  {
    id: 'prj02',
    code: 'PROJ-2026-002',
    name: 'Facility Expansion — Building C',
    description:
      'Construction of a new 12,000 sq ft warehouse annex to support increased inventory capacity and cold-chain storage requirements.',
    status: 'active',
    budget: 1250000,
    currency: 'CAD',
    startDate: '2026-02-01',
    endDate: '2026-12-31',
    ownerDept: 'Operations',
    managerName: 'Derek Olafsson',
    createdAt: '2026-01-15',
  },
  {
    id: 'prj03',
    code: 'PROJ-2026-003',
    name: 'Quality Management System Rollout',
    description:
      'Implementation of ISO 9001:2015 compliant QMS across all production departments, including documentation, audits, and certification.',
    status: 'active',
    budget: 95000,
    currency: 'CAD',
    startDate: '2026-03-01',
    endDate: '2026-08-31',
    ownerDept: 'Quality Assurance',
    managerName: 'Sandra Thibodeau',
    createdAt: '2026-02-20',
  },
  {
    id: 'prj04',
    code: 'PROJ-2025-011',
    name: 'Fleet Electrification Pilot',
    description:
      'Pilot program to replace 10 diesel delivery vehicles with electric alternatives, including charging infrastructure installation.',
    status: 'on_hold',
    budget: 620000,
    currency: 'CAD',
    startDate: '2025-10-01',
    endDate: '2026-06-30',
    ownerDept: 'Logistics',
    managerName: 'James Fitzpatrick',
    createdAt: '2025-09-15',
  },
  {
    id: 'prj05',
    code: 'PROJ-2025-004',
    name: 'Legacy Reporting System Decommission',
    description:
      'Decommissioning of the on-premise Crystal Reports server and migration of all standard reports to Power BI embedded dashboards.',
    status: 'closed',
    budget: 45000,
    currency: 'CAD',
    startDate: '2025-04-01',
    endDate: '2025-12-31',
    ownerDept: 'Finance',
    managerName: 'Luc Tremblay',
    createdAt: '2025-03-20',
  },
]

interface ProjectStoreState {
  projects: Project[]
  addProject: (data: Omit<Project, 'id' | 'createdAt'>) => Project
  updateProject: (id: string, patch: Partial<Omit<Project, 'id' | 'createdAt'>>) => void
  deleteProject: (id: string) => void
}

export const useProjectStore = create<ProjectStoreState>()(
  persist(
    (set) => ({
      projects: DEMO_PROJECTS,

      addProject: (data) => {
        const newProject: Project = {
          ...data,
          id: crypto.randomUUID(),
          createdAt: new Date().toISOString().slice(0, 10),
        }
        set((s) => ({ projects: [...s.projects, newProject] }))
        return newProject
      },

      updateProject: (id, patch) => {
        set((s) => ({
          projects: s.projects.map((p) => (p.id === id ? { ...p, ...patch } : p)),
        }))
      },

      deleteProject: (id) => {
        set((s) => ({ projects: s.projects.filter((p) => p.id !== id) }))
      },
    }),
    { name: 'epms-projects' }
  )
)
