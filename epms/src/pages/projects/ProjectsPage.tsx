import { useState } from 'react'
import {
  Search, Plus, Pencil, Trash2, X, Check, Filter, FolderKanban,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn, todayISO } from '@/lib/utils'
import { formatAmount, formatDate } from '@/lib/utils'
import { useProjects, useCreateProject, useUpdateProject } from '@/hooks/useProjects'
import type { ApiProject, CreateProjectBody, UpdateProjectBody, ProjectStatus } from '@/services/projects'
import { useAuthStore } from '@/stores/auth.store'
import type { UserRole } from '@/types'
import { Segmented, Select, SortTh, TBody, THead, Table, Td, Th, Tr, useSortableRows, Badge, type BadgeTone } from '@uniops/shell'

const PROJECT_TH = 'px-4 py-3 text-left text-xs font-semibold text-fg-muted whitespace-nowrap'

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

const STATUS_TONE: Record<ProjectStatus, BadgeTone> = {
  active: 'success',
  on_hold: 'warning',
  closed: 'neutral',
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
  const year = Number(todayISO().slice(0, 4))   // the plant's year
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

  return (
    <div className="rounded-xl border border-primary-200 bg-primary-50/60 p-5 mb-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        <Button aria-label="Close" variant="subtle" size="icon-xs" onClick={onCancel}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {/* Code */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">
            Project Code <span className="text-danger-fg">*</span>
          </label>
          <Input
            error={!!errors.code}
            value={form.code}
            onChange={(e) => set('code', e.target.value)}
            placeholder="PROJ-2026-001"
          />
          {errors.code && <p className="text-xs text-danger-fg">{errors.code}</p>}
        </div>

        {/* Name */}
        <div className="flex flex-col gap-1.5 sm:col-span-1 lg:col-span-2">
          <label className="text-xs font-medium text-fg-secondary">
            Project Name <span className="text-danger-fg">*</span>
          </label>
          <Input
            error={!!errors.name}
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder="ERP System Upgrade"
          />
          {errors.name && <p className="text-xs text-danger-fg">{errors.name}</p>}
        </div>

        {/* Description */}
        <div className="flex flex-col gap-1.5 sm:col-span-2 lg:col-span-3">
          <label className="text-xs font-medium text-fg-secondary">Description</label>
          <Input
            value={form.description}
            onChange={(e) => set('description', e.target.value)}
            placeholder="Brief description of project scope and objectives"
          />
        </div>

        {/* Owner Department */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">
            Owner Department <span className="text-danger-fg">*</span>
          </label>
          <Input
            error={!!errors.ownerDept}
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
          {errors.ownerDept && <p className="text-xs text-danger-fg">{errors.ownerDept}</p>}
        </div>

        {/* Manager Name */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">
            Project Manager <span className="text-danger-fg">*</span>
          </label>
          <Input
            error={!!errors.managerName}
            value={form.managerName}
            onChange={(e) => set('managerName', e.target.value)}
            placeholder="Jane Smith"
          />
          {errors.managerName && <p className="text-xs text-danger-fg">{errors.managerName}</p>}
        </div>

        {/* Status */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Status</label>
          <Select
            value={form.status}
            onChange={(e) => set('status', e.target.value as ProjectStatus)}
          >
            <option value="active">Active</option>
            <option value="on_hold">On Hold</option>
            <option value="closed">Closed</option>
          </Select>
        </div>

        {/* Budget */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">
            Budget <span className="text-danger-fg">*</span>
          </label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-xs text-fg-muted">$</span>
            <Input
              type="number"
              min="0"
              step="0.01"
              error={!!errors.budget} className="pl-6"
              value={form.budget}
              onChange={(e) => set('budget', e.target.value)}
              placeholder="0.00"
            />
          </div>
          {errors.budget && <p className="text-xs text-danger-fg">{errors.budget}</p>}
        </div>

        {/* Currency */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Currency</label>
          <Input
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
          <label className="text-xs font-medium text-fg-secondary">
            Start Date <span className="text-danger-fg">*</span>
          </label>
          <Input
            type="date"
            error={!!errors.startDate}
            value={form.startDate}
            onChange={(e) => set('startDate', e.target.value)}
          />
          {errors.startDate && <p className="text-xs text-danger-fg">{errors.startDate}</p>}
        </div>

        {/* End Date */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">
            End Date <span className="text-danger-fg">*</span>
          </label>
          <Input
            type="date"
            error={!!errors.endDate}
            value={form.endDate}
            onChange={(e) => set('endDate', e.target.value)}
          />
          {errors.endDate && <p className="text-xs text-danger-fg">{errors.endDate}</p>}
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
  // The whole project list is loaded (GET /projects is not paged), so sorting
  // the rows here is exact.
  const { rows: sortedProjects, sort, onSort } = useSortableRows(filtered, {
    code: (p) => p.code,
    name: (p) => p.name,
    owner_dept: (p) => p.owner_dept,
    manager_name: (p) => p.manager_name,
    budget: (p) => Number(p.budget),
    start_date: (p) => p.start_date,
    end_date: (p) => p.end_date,
    status: (p) => STATUS_LABELS[p.status],
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
          <h1 className="text-2xl font-semibold text-fg">Projects</h1>
          <p className="mt-0.5 text-sm text-fg-muted">
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
        <div className="rounded-xl bg-surface shadow-sm px-4 py-3">
          <p className="text-xs text-fg-muted font-medium">Total</p>
          <p className="mt-1 text-2xl font-semibold text-fg">{totalProjects}</p>
        </div>
        <div className="rounded-xl bg-surface shadow-sm px-4 py-3">
          <p className="text-xs text-fg-muted font-medium">Active</p>
          <p className="mt-1 text-2xl font-semibold text-success-fg">{activeProjects}</p>
        </div>
        <div className="rounded-xl bg-surface shadow-sm px-4 py-3">
          <p className="text-xs text-fg-muted font-medium">On Hold</p>
          <p className="mt-1 text-2xl font-semibold text-warning-fg">{onHoldProjects}</p>
        </div>
        <div className="rounded-xl bg-surface shadow-sm px-4 py-3">
          <p className="text-xs text-fg-muted font-medium">Closed</p>
          <p className="mt-1 text-2xl font-semibold text-fg-muted">{closedProjects}</p>
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-52">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
          <Input
            placeholder="Search code, name, department, manager…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-fg-muted" />
          {/* Status tabs */}
          <Segmented
            items={STATUS_TABS.map((tab) => ({
              key: tab.key,
              label: <>{tab.label}<span className="ml-1.5 font-mono tabular-nums text-fg-muted">{tab.count}</span></>,
            }))}
            value={statusTab}
            onChange={setStatusTab}
          />
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
      <div className="rounded-xl bg-surface shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <Table>
            <THead>
              <Tr className="bg-surface-subtle">
                <SortTh label="Code" sortKey="code" sort={sort} onSort={onSort} className={PROJECT_TH} />
                <SortTh label="Name" sortKey="name" sort={sort} onSort={onSort} className={PROJECT_TH} />
                <SortTh label="Department" sortKey="owner_dept" sort={sort} onSort={onSort} className={PROJECT_TH} />
                <SortTh label="Manager" sortKey="manager_name" sort={sort} onSort={onSort} className={PROJECT_TH} />
                <SortTh label="Budget" sortKey="budget" sort={sort} onSort={onSort} className={PROJECT_TH} />
                <SortTh label="Start" sortKey="start_date" sort={sort} onSort={onSort} className={PROJECT_TH} />
                <SortTh label="End" sortKey="end_date" sort={sort} onSort={onSort} className={PROJECT_TH} />
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className={PROJECT_TH} />
                {canWrite && <Th className={PROJECT_TH} />}
              </Tr>
            </THead>
            <TBody>
              {filtered.length === 0 && (
                <Tr>
                  <Td align="center" colSpan={canWrite ? 9 : 8}>
                    <FolderKanban className="h-8 w-8 text-fg-faint mx-auto mb-3" />
                    <p className="text-sm font-medium text-fg-muted">No projects found</p>
                    <p className="text-xs text-fg-muted mt-1">
                      {search || statusTab !== 'all'
                        ? 'Try adjusting your filters'
                        : 'Add your first project to get started'}
                    </p>
                  </Td>
                </Tr>
              )}
              {sortedProjects.map((project, i) => (
                <Tr
                  key={project.id}
                  className={cn(
                    'border-b border-line-subtle hover:bg-primary-50/40 transition-colors',
                    i % 2 === 1 ? 'bg-surface-subtle/60' : 'bg-surface',
                    project.status === 'closed' && 'opacity-60'
                  )}
                >
                  {/* Code */}
                  <Td>
                    <Badge variant="accent" className="font-mono">
                      {project.code}
                    </Badge>
                  </Td>

                  {/* Name */}
                  <Td className="text-fg font-medium max-w-52">
                    <p className="truncate">{project.name}</p>
                    {project.description && (
                      <p className="text-xs text-fg-muted truncate">{project.description}</p>
                    )}
                  </Td>

                  {/* Department */}
                  <Td className="text-fg-muted whitespace-nowrap">
                    {project.owner_dept}
                  </Td>

                  {/* Manager */}
                  <Td className="text-fg-secondary whitespace-nowrap">
                    {project.manager_name}
                  </Td>

                  {/* Budget */}
                  <Td align="right" className="font-mono text-sm text-fg whitespace-nowrap">
                    {formatAmount(project.budget, project.currency)}
                  </Td>

                  {/* Start Date */}
                  <Td className="text-fg-muted whitespace-nowrap">
                    {project.start_date ? formatDate(project.start_date) : '—'}
                  </Td>

                  {/* End Date */}
                  <Td className="text-fg-muted whitespace-nowrap">
                    {project.end_date ? formatDate(project.end_date) : '—'}
                  </Td>

                  {/* Status */}
                  <Td>
                    <Badge variant={STATUS_TONE[project.status]}>
                      {STATUS_LABELS[project.status]}
                    </Badge>
                  </Td>

                  {/* Actions */}
                  {canWrite && (
                    <Td>
                      <div className="flex items-center justify-end gap-1">
                        {deleteConfirm === project.id ? (
                          <>
                            <Button aria-label="Confirm delete" variant="subtle" size="icon-xs"
                              onClick={() => {
                                // No delete endpoint on backend — dismiss confirmation
                                setDeleteConfirm(null)
                              }}
                              className="text-danger-fg hover:bg-danger-soft"
                              title="Confirm delete"
                            >
                              <Check className="h-3.5 w-3.5" />
                            </Button>
                            <Button aria-label="Cancel" variant="subtle" size="icon-xs"
                              onClick={() => setDeleteConfirm(null)}
                             
                              title="Cancel"
                            >
                              <X className="h-3.5 w-3.5" />
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button aria-label="Edit project" variant="subtle" size="icon-xs"
                              onClick={() => {
                                openEdit(project)
                                setDeleteConfirm(null)
                              }}
                             
                              title="Edit project"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </Button>
                            {canDelete && (
                              <Button variant="danger-ghost" size="icon-xs" aria-label="Remove"
                                onClick={() => {
                                  setDeleteConfirm(project.id)
                                  setMode('none')
                                }}
                               
                                title="Delete project"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            )}
                          </>
                        )}
                      </div>
                    </Td>
                  )}
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
        <div className="border-t border-line bg-surface-subtle px-4 py-2.5 text-xs text-fg-muted">
          Showing {filtered.length} of {projects.length} projects
        </div>
      </div>
    </div>
  )
}
