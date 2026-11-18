import { type ReactNode } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/store/auth'
import { useRolePermissions } from '@/hooks/useRolePermissions'
import { PortalPageLayout } from '@/components/layout/PortalPageLayout'
import { cn, formatDate } from '@/lib/utils'
import { Badge } from '@uniops/shell'

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
  hire_date: string | null
  pay_type: 'hourly' | 'salaried' | null
  is_manager: boolean
  /** IMP-016: where the latest ADP roster differs, waiting for Follow ADP / Keep Current. */
  adp_pending: AdpDifference[] | null
  adp_pending_since: string | null
}

export interface AdpDifference { field: string; current: string | null; adp: string | null }

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
export const PAY_TYPE_LABEL: Record<string, string> = { hourly: 'Hourly', salaried: 'Salaried' }
export const FIELD_LABEL: Record<string, string> = {
  payroll_name: 'Payroll name', display_name: 'Name', work_email: 'Email', job_title: 'Job title',
  adp_department: 'ADP department', status: 'Status', hire_date: 'Hire date', pay_type: 'Pay type',
}

/** A field value as people read it: labels for codes, the site's date format for dates. */
export function fieldValue(field: string, value: string | null): string {
  if (value === null || value === '') return '—'
  if (field === 'status') return STATUS_LABEL[value] ?? value
  if (field === 'pay_type') return PAY_TYPE_LABEL[value] ?? value
  if (field === 'hire_date') return formatDate(value)
  return value
}
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
    <Badge variant={status === 'active' ? 'success' : status === 'on_leave' ? 'warning' : 'neutral'}>
      {STATUS_LABEL[status] ?? status}
    </Badge>
  )
}

export function NcPill({ state }: { state: string }) {
  return (
    <Badge variant={state === 'in_step' ? 'success' : state === 'unknown' ? 'neutral' : 'warning'}>
      {NC_STATE_LABEL[state] ?? state}
    </Badge>
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
        <div className="mb-5 flex gap-1 border-b border-line">
          {TABS.filter((t) => can(t.permission)).map((t) => (
            <Link key={t.to} to={t.to}
              className={cn('-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors',
                current === t.to ? 'border-primary-700 text-primary-700' : 'border-transparent text-fg-muted hover:text-fg')}>
              {t.label}
            </Link>
          ))}
        </div>
        {children}
      </div>
    </PortalPageLayout>
  )
}
