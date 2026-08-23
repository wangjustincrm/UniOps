import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { User, UserRole } from '@/types'
import { todayISODate } from '@/lib/utils'

export interface ManagedUser extends User {
  teamsAccount: string
  isActive: boolean
  createdAt: string
  password: string              // plaintext for prototype
  passwordChangedAt: string     // ISO date — used for expiry checks
  mustChangePassword: boolean   // true after admin reset
}

const INITIAL_USERS: ManagedUser[] = [
  { id: '1', name: 'Jane Smith',    email: 'requester@epms.ca',   role: 'requester',           department: 'Marketing',   teamsAccount: 'jane.smith@company.ca',   isActive: true, createdAt: '2026-01-10', password: 'demo1234', passwordChangedAt: '2026-01-10', mustChangePassword: false },
  { id: '2', name: 'John Brown',    email: 'manager@epms.ca',     role: 'dept_manager',        department: 'Marketing',   teamsAccount: 'john.brown@company.ca',   isActive: true, createdAt: '2026-01-10', password: 'demo1234', passwordChangedAt: '2026-01-10', mustChangePassword: false },
  { id: '3', name: 'Mike Chen',     email: 'procurement@epms.ca', role: 'procurement_officer', department: 'Procurement', teamsAccount: 'mike.chen@company.ca',     isActive: true, createdAt: '2026-01-10', password: 'demo1234', passwordChangedAt: '2026-01-10', mustChangePassword: false },
  { id: '4', name: 'Mike Johnson',  email: 'warehouse@epms.ca',   role: 'warehouse_staff',     department: 'Operations',  teamsAccount: 'mike.johnson@company.ca',  isActive: true, createdAt: '2026-01-10', password: 'demo1234', passwordChangedAt: '2026-01-10', mustChangePassword: false },
  { id: '5', name: 'Sarah Lee',     email: 'ap@epms.ca',          role: 'ap_clerk',            department: 'Finance',     teamsAccount: 'sarah.lee@company.ca',     isActive: true, createdAt: '2026-01-10', password: 'demo1234', passwordChangedAt: '2026-01-10', mustChangePassword: false },
  { id: '6', name: 'David Wang',    email: 'finance@epms.ca',     role: 'finance_manager',     department: 'Finance',     teamsAccount: 'david.wang@company.ca',    isActive: true, createdAt: '2026-01-10', password: 'demo1234', passwordChangedAt: '2026-01-10', mustChangePassword: false },
  { id: '7', name: 'Sarah Chen',    email: 'gm@epms.ca',          role: 'gm',                  department: 'Executive',   teamsAccount: 'sarah.chen@company.ca',    isActive: true, createdAt: '2026-01-10', password: 'demo1234', passwordChangedAt: '2026-01-10', mustChangePassword: false },
  { id: '8', name: 'System Admin',  email: 'admin@epms.ca',       role: 'system_admin',        department: 'IT',          teamsAccount: '',                         isActive: true, createdAt: '2026-01-10', password: 'demo1234', passwordChangedAt: '2026-01-10', mustChangePassword: false },
  { id: '9', name: 'Tom Li',        email: 'po.manager@epms.ca',  role: 'procurement_manager', department: 'Procurement', teamsAccount: 'tom.li@company.ca',        isActive: true, createdAt: '2026-01-10', password: 'demo1234', passwordChangedAt: '2026-01-10', mustChangePassword: false },
]

interface UserStoreState {
  users: ManagedUser[]
  addUser: (data: Omit<ManagedUser, 'id' | 'createdAt'>) => ManagedUser
  updateUser: (id: string, patch: Partial<Omit<ManagedUser, 'id' | 'createdAt'>>) => void
  deleteUser: (id: string) => void
  changePassword: (id: string, newPassword: string) => void
  resetPassword: (id: string, tempPassword: string) => void
}

export const useUserStore = create<UserStoreState>()(
  persist(
    (set, get) => ({
      users: INITIAL_USERS,

      addUser: (data) => {
        const newUser: ManagedUser = {
          ...data,
          id: crypto.randomUUID(),
          createdAt: todayISODate(),
        }
        set((s) => ({ users: [...s.users, newUser] }))
        return newUser
      },

      updateUser: (id, patch) => {
        set((s) => ({
          users: s.users.map((u) => (u.id === id ? { ...u, ...patch } : u)),
        }))
      },

      deleteUser: (id) => {
        set((s) => ({ users: s.users.filter((u) => u.id !== id) }))
      },

      changePassword: (id, newPassword) => {
        get().updateUser(id, { password: newPassword, passwordChangedAt: todayISODate(), mustChangePassword: false })
      },

      resetPassword: (id, tempPassword) => {
        get().updateUser(id, { password: tempPassword, passwordChangedAt: todayISODate(), mustChangePassword: true })
      },
    }),
    {
      name: 'epms-users',
      // Always ensure the system admin account exists after hydration
      merge: (persisted, current) => {
        const state = persisted as UserStoreState
        const hasAdmin = state.users?.some((u) => u.role === 'system_admin' && u.isActive)
        const users = hasAdmin
          ? state.users
          : [...(state.users ?? []), INITIAL_USERS.find((u) => u.id === '8')!]
        return { ...current, users }
      },
    }
  )
)

export const ROLE_LABELS: Record<UserRole, string> = {
  requester: 'Requester',
  dept_manager: 'Department Manager',
  gm: 'General Manager',
  opm: 'Operations Manager',
  procurement_officer: 'Procurement Officer',
  procurement_manager: 'Procurement Manager',
  warehouse_staff: 'Warehouse Staff',
  ap_clerk: 'AP Clerk',
  finance_manager: 'Finance Manager',
  finance_bp: 'Finance Business Partner',
  cfo: 'CFO',
  auditor: 'Auditor',
  vendor_manager: 'Vendor Manager',
  erp_pa_officer: 'ERP PA Officer',
  payment_officer: 'Payment Officer',
  system_admin: 'System Admin',
}
