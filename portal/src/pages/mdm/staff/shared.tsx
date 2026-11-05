import { type ReactNode } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/store/auth'
import { useRolePermissions } from '@/hooks/useRolePermissions'
import { PortalPageLayout } from '@/components/layout/PortalPageLayout'
import { cn } from '@/lib/utils'

export interface StaffRow {
  id: string
  employee_number: string
  source: 'adp' | 'non_adp'
  display_name: string
  payroll_name: string
  work_email: string | null
  job_title: string | null
  adp_department: string | null
  department_id: string | null
  department_name: string | null
  needs_department: boolean
  status: 'active' | 'on_leave' | 'left'
  left_on: string | null
  user: { id: string; email: string; full_name: string; is_active: boolean } | null
  nc_state: string
  open_todos: number
}

export interface NcTodoRow {
  id: string
  employee_id: string
  employee_number: string
  display_name: string
  type: string
  status: 'open' | 'closed'
  target: {
    nc_department_code?: string; nc_department_name?: string; person_category?: string
    old_code?: string; new_code?: string; description?: string
  }
  from_import: boolean
  created_at: string
  closed_at: string | null
  closed_how: string | null
  close_reason: string | null
  overdue: boolean
}

export interface Department { id: string; code: string; name: string; is_active: boolean }

export const STATUS_LABEL: Record<string, string> = { active: 'Active', on_leave: 'On Leave', left: 'Left' }
export const SOURCE_LABEL: Record<string, string> = { adp: 'ADP', non_adp: 'Non-ADP' }
export const NC_STATE_LABEL: Record<string, string> = {
  unknown: 'Not checked',
  in_step: 'In step',
  not_in_nc: 'Not in NC',
  different_department: 'Different department',
  different_status: 'Different status',
  different_code: 'Different code',
}
export const TODO_LABEL: Record<string, string> = {
  change_department: 'Change department',
  terminate: 'Terminate',
  rehire: 'Rehire',
  change_code: 'Change code',
  fix_mismatch: 'Fix mismatch',
}

export const TH = 'sticky top-0 z-10 bg-neutral-50 px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500 whitespace-nowrap'
export const TD = 'px-4 py-2.5 text-sm text-neutral-700'
export const PRIMARY_BTN = 'inline-flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] transition-colors disabled:opacity-50'
export const SECONDARY_BTN = 'inline-flex items-center gap-1.5 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors disabled:opacity-50'
export const INPUT = 'rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm focus:outline-none focus:border-primary-400 focus:ring-1 focus:ring-primary-200'

/** Effective permission check. system_admin holds every key. */
export function useStaffPerm() {
  const { user } = useAuthStore()
  const query = useRolePermissions()
  const admin = user?.role === 'system_admin'
  return {
    admin,
    loading: !admin && query.isLoading,
    can: (key: string) => admin || !!query.data?.permissions?.[key],
  }
}

function targetText(todo: NcTodoRow): string {
  const t = todo.target
  if (t.description) return t.description
  const parts: string[] = []
  if (t.nc_department_code) parts.push(`${t.nc_department_code} ${t.nc_department_name ?? ''}`.trim())
  if (t.person_category) parts.push(t.person_category)
  if (t.new_code) parts.push(`${t.old_code} → ${t.new_code}`)
  return parts.join(' · ')
}
export { targetText }

export function StatusPill({ status }: { status: string }) {
  return (
    <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium',
      status === 'active' ? 'bg-green-50 text-green-700'
        : status === 'on_leave' ? 'bg-amber-50 text-amber-700' : 'bg-neutral-100 text-neutral-500')}>
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

export function NcPill({ state }: { state: string }) {
  return (
    <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap',
      state === 'in_step' ? 'bg-green-50 text-green-700'
        : state === 'unknown' ? 'bg-neutral-100 text-neutral-500' : 'bg-amber-50 text-amber-700')}>
      {NC_STATE_LABEL[state] ?? state}
    </span>
  )
}

const TABS = [
  { to: '/mdm/staff', label: 'Staff', permission: 'employee.view' },
  { to: '/mdm/staff/import', label: 'Import', permission: 'employee.import' },
  { to: '/mdm/staff/department-mapping', label: 'Department Mapping', permission: 'employee.view' },
  { to: '/mdm/staff/nc-todo', label: 'NC To-do', permission: 'employee.view' },
]

/** Portal → MDM → Staff: the page shell, permission gate and the four tabs. */
export function StaffShell({ permission, children }: { permission: string; children: ReactNode }) {
  const { can, loading } = useStaffPerm()
  const { pathname } = useLocation()
  if (loading) return null
  if (!can(permission)) return <Navigate to="/" replace />
  const current = [...TABS].reverse().find((t) => pathname.startsWith(t.to))?.to
  return (
    <PortalPageLayout activeKey="portal:/mdm/staff">
      <div className="p-6">
        <div className="mb-5 flex gap-1 border-b border-neutral-200">
          {TABS.filter((t) => can(t.permission)).map((t) => (
            <Link key={t.to} to={t.to}
              className={cn('-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors',
                current === t.to ? 'border-[#085E5E] text-[#085E5E]' : 'border-transparent text-neutral-500 hover:text-neutral-800')}>
              {t.label}
            </Link>
          ))}
        </div>
        {children}
      </div>
    </PortalPageLayout>
  )
}
