import { useState } from 'react'
import {
  Search, Plus, Pencil, Trash2, X, Check, Filter, FolderKanban,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { formatAmount, formatDate } from '@/lib/utils'
import { useProjects, useCreateProject, useUpdateProject } from '@/hooks/useProjects'
import type { ApiProject, CreateProjectBody, UpdateProjectBody, ProjectStatus } from '@/services/projects'
import { useAuthStore } from '@/stores/auth.store'
import type { UserRole } from '@/types'

// ─── Constants ────────────────────────────────────────────────────────────────

const ALLOWED_WRITE_ROLES: UserRole[] = [
  'procurement_officer',
  'procurement_manager',
  'system_admin',
]

const ALLOWED_DELETE_ROLES: UserRole[] = ['procurement_manager', 'system_admin']

const CURRENCIES = ['CAD', 'USD', 'EUR', 'GBP']

const DEPARTMENTS = [
  'Information Technology',
  'Operations',
  'Finance',
  'Procurement',
  'Human Resources',
  'Quality Assurance',
  'Logistics',
  'Maintenance',
  'Marketing',
  'Executive',
]

type StatusFilter = 'all' | ProjectStatus

const STATUS_LABELS: Record<ProjectStatus, string> = {
  active: 'Active',
  on_hold: 'On Hold',
  closed: 'Closed',
}

const STATUS_BADGE: Record<ProjectStatus, string> = {
  active: 'bg-success-50 text-success-700',
  on_hold: 'bg-warning-50 text-warning-700',
  closed: 'bg-neutral-100 text-neutral-500',
}

// ─── Form types ───────────────────────────────────────────────────────────────

interface ProjectFormData {
  code: string
  name: string
  description: string
  status: ProjectStatus
  budget: string
  currency: string
  startDate: string
  endDate: string
  ownerDept: string
  managerName: string
}

const BLANK_FORM: ProjectFormData = {
  code: '',
  name: '',
  description: '',
  status: 'active',
  budget: '',
  currency: 'CAD',
  startDate: '',
  endDate: '',
  ownerDept: '',
  managerName: '',
}

function projectToForm(p: ApiProject): ProjectFormData {
  return {
    code: p.code,
    name: p.name,
    description: p.description ?? '',
    status: p.status,
    budget: String(p.budget),
    currency: p.currency,
    startDate: p.start_date ?? '',
    endDate: p.end_date ?? '',
    ownerDept: p.owner_dept ?? '',
    managerName: p.manager_name ?? '',
  }
}

function nextProjectCode(projects: ApiProject[]): string {
  const year = new Date().getFullYear()
  const yearProjects = projects.filter((p) => p.code.includes(String(year)))
  const nums = yearProjects
    .map((p) => {
      const parts = p.code.split('-')
      return parseInt(parts[parts.length - 1], 10)
    })
    .filter((n) => !isNaN(n))
  const next = nums.length > 0 ? Math.max(...nums) + 1 : 1
  return `PROJ-${year}-${String(next).padStart(3, '0')}`
}

// ─── Project Form Component ───────────────────────────────────────────────────

interface ProjectFormProps {
  form: ProjectFormData
  onChange: (f: ProjectFormData) => void
  onSave: () => void
  onCancel: () => void
  title: string
  errors: Partial<Record<keyof ProjectFormData, string>>
}

function ProjectForm({ form, onChange, onSave, onCancel, title, errors }: ProjectFormProps) {
  const set = (k: keyof ProjectFormData, v: string) => onChange({ ...form, [k]: v })

  const inputCls = (err?: string) =>
    cn(
      'h-10 w-full rounded-lg border bg-neutral-100 px-3 text-sm focus:outline-none focus:bg-white focus:border-primary-600 focus:shadow-[0_0_0_3px_rgba(10,124,124,0.10)] transition-colors',
      err ? 'border-danger-600 bg-danger-50' : 'border-neutral-200'
    )

  return (
    <div className="rounded-xl border border-primary-200 bg-primary-50/60 p-5 mb-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-neutral-900">{title}</h3>
        <button onClick={onCancel} className="text-neutral-400 hover:text-neutral-600">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {/* Code */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">
            Project Code <span className="text-danger-600">*</span>
          </label>
          <input
            className={inputCls(errors.code)}
            value={form.code}
            onChange={(e) => set('code', e.target.value)}
            placeholder="PROJ-2026-001"
          />
          {errors.code && <p className="text-xs text-danger-600">{errors.code}</p>}
        </div>

        {/* Name */}
        <div className="flex flex-col gap-1.5 sm:col-span-1 lg:col-span-2">
          <label className="text-xs font-medium text-neutral-700">
            Project Name <span className="text-danger-600">*</span>
          </label>
          <input
            className={inputCls(errors.name)}
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder="ERP System Upgrade"
          />
          {errors.name && <p className="text-xs text-danger-600">{errors.name}</p>}
        </div>

        {/* Description */}
        <div className="flex flex-col gap-1.5 sm:col-span-2 lg:col-span-3">
          <label className="text-xs font-medium text-neutral-700">Description</label>
          <input
            className={inputCls()}
            value={form.description}
            onChange={(e) => set('description', e.target.value)}
            placeholder="Brief description of project scope and objectives"
          />
        </div>

        {/* Owner Department */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">
            Owner Department <span className="text-danger-600">*</span>
          </label>
          <input
            className={inputCls(errors.ownerDept)}
            list="proj-dept-list"
            value={form.ownerDept}
            onChange={(e) => set('ownerDept', e.target.value)}
            placeholder="Operations"
          />
          <datalist id="proj-dept-list">
            {DEPARTMENTS.map((d) => (
              <option key={d} value={d} />
            ))}
          </datalist>
          {errors.ownerDept && <p className="text-xs text-danger-600">{errors.ownerDept}</p>}
        </div>

        {/* Manager Name */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">
            Project Manager <span className="text-danger-600">*</span>
          </label>
          <input
            className={inputCls(errors.managerName)}
            value={form.managerName}
            onChange={(e) => set('managerName', e.target.value)}
            placeholder="Jane Smith"
          />
          {errors.managerName && <p className="text-xs text-danger-600">{errors.managerName}</p>}
        </div>

        {/* Status */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Status</label>
          <select
            className={inputCls()}
            value={form.status}
            onChange={(e) => set('status', e.target.value as ProjectStatus)}
          >
            <option value="active">Active</option>
            <option value="on_hold">On Hold</option>
            <option value="closed">Closed</option>
          </select>
        </div>

        {/* Budget */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">
            Budget <span className="text-danger-600">*</span>
          </label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-xs text-neutral-400">$</span>
            <input
              type="number"
              min="0"
              step="0.01"
              className={cn(inputCls(errors.budget), 'pl-6')}
              value={form.budget}
              onChange={(e) => set('budget', e.target.value)}
              placeholder="0.00"
            />
          </div>
          {errors.budget && <p className="text-xs text-danger-600">{errors.budget}</p>}
        </div>

        {/* Currency */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Currency</label>
          <input
            className={inputCls()}
            list="proj-currency-list"
            value={form.currency}
            onChange={(e) => set('currency', e.target.value.toUpperCase())}
            placeholder="CAD"
            maxLength={5}
          />
          <datalist id="proj-currency-list">
            {CURRENCIES.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </div>

        {/* Start Date */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">
            Start Date <span className="text-danger-600">*</span>
          </label>
          <input
            type="date"
            className={inputCls(errors.startDate)}
            value={form.startDate}
            onChange={(e) => set('startDate', e.target.value)}
          />
          {errors.startDate && <p className="text-xs text-danger-600">{errors.startDate}</p>}
        </div>

        {/* End Date */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">
            End Date <span className="text-danger-600">*</span>
          </label>
          <input
            type="date"
            className={inputCls(errors.endDate)}
            value={form.endDate}
            onChange={(e) => set('endDate', e.target.value)}
          />
          {errors.endDate && <p className="text-xs text-danger-600">{errors.endDate}</p>}
        </div>
      </div>

      <div className="mt-4 flex justify-end gap-2 border-t border-primary-200 pt-4">
        <Button variant="secondary" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={onSave}>
          <Check className="h-3.5 w-3.5" />
          Save Project
        </Button>
      </div>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function ProjectsPage() {
  const { data: projects = [] } = useProjects()
  const createProject = useCreateProject()
  const updateProject = useUpdateProject()
  const { user } = useAuthStore()

  const canWrite = user ? ALLOWED_WRITE_ROLES.includes(user.role) : false
  const canDelete = user ? ALLOWED_DELETE_ROLES.includes(user.role) : false

  const [search, setSearch] = useState('')
  const [statusTab, setStatusTab] = useState<StatusFilter>('all')
  const [mode, setMode] = useState<'none' | 'add' | { edit: string }>('none')
  const [formData, setFormData] = useState<ProjectFormData>(BLANK_FORM)
  const [formErrors, setFormErrors] = useState<Partial<Record<keyof ProjectFormData, string>>>({})
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  // ── Stats ───────────────────────────────────────────────────────────────────
  const totalProjects = projects.length
  const activeProjects = projects.filter((p) => p.status === 'active').length
  const onHoldProjects = projects.filter((p) => p.status === 'on_hold').length
  const closedProjects = projects.filter((p) => p.status === 'closed').length

  // ── Filtering ───────────────────────────────────────────────────────────────
  const filtered = projects.filter((p) => {
    const q = search.toLowerCase()
    const matchSearch =
      !q ||
      p.name.toLowerCase().includes(q) ||
      p.code.toLowerCase().includes(q) ||
      (p.owner_dept ?? '').toLowerCase().includes(q) ||
      (p.manager_name ?? '').toLowerCase().includes(q)
    const matchStatus = statusTab === 'all' || p.status === statusTab
    return matchSearch && matchStatus
  })

  // ── Validation ──────────────────────────────────────────────────────────────
  const validate = (f: ProjectFormData): boolean => {
    const e: Partial<Record<keyof ProjectFormData, string>> = {}
    if (!f.code.trim()) e.code = 'Required'
    if (!f.name.trim()) e.name = 'Required'
    if (!f.ownerDept.trim()) e.ownerDept = 'Required'
    if (!f.managerName.trim()) e.managerName = 'Required'
    const budget = parseFloat(f.budget)
    if (isNaN(budget) || budget < 0) e.budget = 'Must be a valid number ≥ 0'
    if (!f.startDate) e.startDate = 'Required'
    if (!f.endDate) e.endDate = 'Required'
    if (f.startDate && f.endDate && f.endDate < f.startDate) {
      e.endDate = 'End date must be on or after start date'
    }
    setFormErrors(e)
    return Object.keys(e).length === 0
  }

  const openAdd = () => {
    setFormData({ ...BLANK_FORM, code: nextProjectCode(projects) })
    setFormErrors({})
    setMode('add')
    setDeleteConfirm(null)
  }

  const openEdit = (project: ApiProject) => {
    setFormData(projectToForm(project))
    setFormErrors({})
    setMode({ edit: project.id })
    setDeleteConfirm(null)
  }

  const handleSave = () => {
    if (!validate(formData)) return
    if (mode === 'add') {
      const body: CreateProjectBody = {
        code: formData.code.trim(),
        name: formData.name.trim(),
        description: formData.description.trim() || undefined,
        status: formData.status,
        budget: parseFloat(formData.budget),
        currency: formData.currency.trim() || 'CAD',
        start_date: formData.startDate || undefined,
        end_date: formData.endDate || undefined,
        owner_dept: formData.ownerDept.trim() || undefined,
        manager_name: formData.managerName.trim() || undefined,
      }
      createProject.mutate(body)
    } else if (typeof mode === 'object' && 'edit' in mode) {
      const body: UpdateProjectBody = {
        name: formData.name.trim(),
        description: formData.description.trim() || undefined,
        status: formData.status,
        budget: parseFloat(formData.budget),
        currency: formData.currency.trim() || 'CAD',
        start_date: formData.startDate || undefined,
        end_date: formData.endDate || undefined,
        owner_dept: formData.ownerDept.trim() || undefined,
        manager_name: formData.managerName.trim() || undefined,
      }
      updateProject.mutate({ id: mode.edit, body })
    }
    setMode('none')
  }

  const STATUS_TABS: { key: StatusFilter; label: string; count: number }[] = [
    { key: 'all', label: 'All', count: totalProjects },
    { key: 'active', label: 'Active', count: activeProjects },
    { key: 'on_hold', label: 'On Hold', count: onHoldProjects },
    { key: 'closed', label: 'Closed', count: closedProjects },
  ]

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Projects</h1>
          <p className="mt-0.5 text-sm text-neutral-500">
            Track and manage procurement projects and budgets
          </p>
        </div>
        {canWrite && (
          <Button onClick={openAdd} disabled={mode !== 'none'}>
            <Plus className="h-4 w-4" />
            Add Project
          </Button>
        )}
      </div>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] px-4 py-3">
          <p className="text-xs text-neutral-500 font-medium uppercase tracking-wide">Total</p>
          <p className="mt-1 text-2xl font-bold text-neutral-900">{totalProjects}</p>
        </div>
        <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] px-4 py-3">
          <p className="text-xs text-neutral-500 font-medium uppercase tracking-wide">Active</p>
          <p className="mt-1 text-2xl font-bold text-success-600">{activeProjects}</p>
        </div>
        <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] px-4 py-3">
          <p className="text-xs text-neutral-500 font-medium uppercase tracking-wide">On Hold</p>
          <p className="mt-1 text-2xl font-bold text-warning-600">{onHoldProjects}</p>
        </div>
        <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] px-4 py-3">
          <p className="text-xs text-neutral-500 font-medium uppercase tracking-wide">Closed</p>
          <p className="mt-1 text-2xl font-bold text-neutral-400">{closedProjects}</p>
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-52">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
          <Input
            placeholder="Search code, name, department, manager…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-neutral-400" />
          {/* Status tabs */}
          <div className="flex rounded-lg border border-neutral-200 overflow-hidden">
            {STATUS_TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setStatusTab(tab.key)}
                className={cn(
                  'px-3.5 py-2 text-xs font-medium transition-colors',
                  statusTab === tab.key
                    ? 'bg-primary-600 text-white'
                    : 'bg-white text-neutral-600 hover:bg-neutral-50'
                )}
              >
                {tab.label}
                <span
                  className={cn(
                    'ml-1.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold',
                    statusTab === tab.key
                      ? 'bg-white/20 text-white'
                      : 'bg-neutral-100 text-neutral-500'
                  )}
                >
                  {tab.count}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Add / Edit form */}
      {mode !== 'none' && canWrite && (
        <ProjectForm
          title={
            mode === 'add'
              ? 'Add New Project'
              : `Edit — ${formData.name || formData.code}`
          }
          form={formData}
          errors={formErrors}
          onChange={setFormData}
          onSave={handleSave}
          onCancel={() => setMode('none')}
        />
      )}

      {/* Table */}
      <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                {[
                  'Code',
                  'Name',
                  'Department',
                  'Manager',
                  'Budget',
                  'Start',
                  'End',
                  'Status',
                  ...(canWrite ? [''] : []),
                ].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={canWrite ? 9 : 8} className="py-16 text-center">
                    <FolderKanban className="h-8 w-8 text-neutral-300 mx-auto mb-3" />
                    <p className="text-sm font-medium text-neutral-500">No projects found</p>
                    <p className="text-xs text-neutral-400 mt-1">
                      {search || statusTab !== 'all'
                        ? 'Try adjusting your filters'
                        : 'Add your first project to get started'}
                    </p>
                  </td>
                </tr>
              )}
              {filtered.map((project, i) => (
                <tr
                  key={project.id}
                  className={cn(
                    'border-b border-neutral-100 hover:bg-primary-50/40 transition-colors',
                    i % 2 === 1 ? 'bg-neutral-50/60' : 'bg-white',
                    project.status === 'closed' && 'opacity-60'
                  )}
                >
                  {/* Code */}
                  <td className="px-4 py-3">
                    <span className="font-mono text-xs font-semibold text-primary-700 bg-primary-50 px-1.5 py-0.5 rounded">
                      {project.code}
                    </span>
                  </td>

                  {/* Name */}
                  <td className="px-4 py-3 text-neutral-900 font-medium max-w-52">
                    <p className="truncate">{project.name}</p>
                    {project.description && (
                      <p className="text-xs text-neutral-400 truncate">{project.description}</p>
                    )}
                  </td>

                  {/* Department */}
                  <td className="px-4 py-3 text-neutral-500 whitespace-nowrap">
                    {project.owner_dept}
                  </td>

                  {/* Manager */}
                  <td className="px-4 py-3 text-neutral-600 whitespace-nowrap">
                    {project.manager_name}
                  </td>

                  {/* Budget */}
                  <td className="px-4 py-3 font-mono text-sm text-neutral-900 text-right whitespace-nowrap">
                    {formatAmount(project.budget, project.currency)}
                  </td>

                  {/* Start Date */}
                  <td className="px-4 py-3 text-neutral-500 whitespace-nowrap">
                    {project.start_date ? formatDate(project.start_date) : '—'}
                  </td>

                  {/* End Date */}
                  <td className="px-4 py-3 text-neutral-500 whitespace-nowrap">
                    {project.end_date ? formatDate(project.end_date) : '—'}
                  </td>

                  {/* Status */}
                  <td className="px-4 py-3">
                    <span
                      className={cn(
                        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
                        STATUS_BADGE[project.status]
                      )}
                    >
                      {STATUS_LABELS[project.status]}
                    </span>
                  </td>

                  {/* Actions */}
                  {canWrite && (
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        {deleteConfirm === project.id ? (
                          <>
                            <button
                              onClick={() => {
                                // No delete endpoint on backend — dismiss confirmation
                                setDeleteConfirm(null)
                              }}
                              className="flex h-7 w-7 items-center justify-center rounded text-danger-600 hover:bg-danger-50"
                              title="Confirm delete"
                            >
                              <Check className="h-3.5 w-3.5" />
                            </button>
                            <button
                              onClick={() => setDeleteConfirm(null)}
                              className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-neutral-100"
                              title="Cancel"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              onClick={() => {
                                openEdit(project)
                                setDeleteConfirm(null)
                              }}
                              className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-primary-50 hover:text-primary-600"
                              title="Edit project"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            {canDelete && (
                              <button
                                onClick={() => {
                                  setDeleteConfirm(project.id)
                                  setMode('none')
                                }}
                                className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-danger-50 hover:text-danger-500"
                                title="Delete project"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="border-t border-neutral-200 bg-neutral-50 px-4 py-2.5 text-xs text-neutral-500">
          Showing {filtered.length} of {projects.length} projects
        </div>
      </div>
    </div>
  )
}
