import { Fragment, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Plus, Search, UserPlus, X } from 'lucide-react'
import { Button, Checkbox, Input, Modal, Pagination, Select, SortTh, TBody, THead, Table, Td, Th, Tr, sortParams, useSort } from '@uniops/shell'
import { epmsApi, mdmApi, mdmDownload } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { useConfirmDialog } from '@/components/ConfirmDialog'
import {
  FIELD_LABEL, NC_STATE_LABEL, NcPill, PAY_TYPE_LABEL, fieldValue, SOURCE_LABEL, StaffShell, StatusPill, useStaffPerm, type Department, type StaffRow,
} from './shared'

type SortKey = 'employee_number' | 'display_name' | 'work_email' | 'job_title' | 'adp_department' | 'department' | 'status' | 'source' | 'hire_date' | 'pay_type' | 'is_manager' | 'user' | 'nc_state'

interface BulkResult {
  created: { email: string; full_name: string; temp_password: string }[]
  errors: { erp_person_code: string; reason: string }[]
}

const FILTERS = {
  status: { label: 'All statuses', options: [['active', 'Active'], ['on_leave', 'On Leave'], ['left', 'Left']] },
  source: { label: 'All sources', options: [['adp', 'ADP'], ['non_adp', 'Non-ADP']] },
  pay_type: { label: 'Pay type: any', options: [['hourly', 'Hourly'], ['salaried', 'Salaried'], ['none', 'Not set']] },
  is_manager: { label: 'Manager: any', options: [['true', 'Managers'], ['false', 'Not managers']] },
  has_user: { label: 'User: any', options: [['true', 'Has user'], ['false', 'No user']] },
  nc: { label: 'NC: any', options: [['in_step', 'In step'], ['todo_open', 'To-do open'], ['not_in_nc', 'Not in NC']] },
} as const
type FilterKey = keyof typeof FILTERS

export default function StaffList() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { admin, can } = useStaffPerm()
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState<Partial<Record<FilterKey, string>>>({})
  const [departmentId, setDepartmentId] = useState('')
  const [differs, setDiffers] = useState(false)
  const [needsDepartment, setNeedsDepartment] = useState(false)
  const [searchParams] = useSearchParams()
  const [adpDiffers, setAdpDiffers] = useState(searchParams.get('adp_differs') === 'true')
  const dlg = useConfirmDialog()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [sort, onSort] = useSort<SortKey>(null, () => setPage(1))
  const [selected, setSelected] = useState<Record<string, StaffRow>>({})
  const [bulk, setBulk] = useState<BulkResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [adding, setAdding] = useState(false)

  const params = new URLSearchParams()
  if (search) params.set('search', search)
  for (const [k, v] of Object.entries(filters)) if (v) params.set(k, v)
  if (departmentId) params.set('department_id', departmentId)
  if (differs) params.set('differs', 'true')
  if (needsDepartment) params.set('needs_department', 'true')
  if (adpDiffers) params.set('adp_differs', 'true')
  const filterQs = params.toString()
  const qs = new URLSearchParams(params)
  qs.set('page', String(page))
  qs.set('page_size', String(pageSize))
  for (const [k, v] of Object.entries(sortParams(sort))) qs.set(k, String(v))

  const { data, isLoading } = useQuery<{ items: StaffRow[]; total: number; adp_differs: number }>({
    queryKey: ['staff', qs.toString()],
    queryFn: () => mdmApi.get(`/staff?${qs}`),
    placeholderData: (prev) => prev,
  })
  const { data: depts } = useQuery<{ items: Department[] }>({
    queryKey: ['portal-departments'],
    queryFn: () => mdmApi.get('/departments'),
  })

  const canCreate = (r: StaffRow) => admin && !r.user && r.status !== 'left'
  const picked = Object.values(selected)
  const toggle = (r: StaffRow) => setSelected((s) => {
    const next = { ...s }
    if (next[r.id]) delete next[r.id]; else next[r.id] = r
    return next
  })

  const bulkCreate = async () => {
    if (busy) return
    setBusy(true); setError('')
    try {
      const result = await epmsApi.post<BulkResult>('/users/bulk-create', {
        items: picked.map((r) => ({
          erp_person_code: r.employee_number, email: r.work_email, full_name: r.display_name,
          department_id: r.department_id,
        })),
      })
      setBulk(result)
      setSelected({})
      qc.invalidateQueries({ queryKey: ['staff'] })
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // IMP-016: an import leaves existing employees alone; each difference is decided here.
  const decide = async (action: 'follow' | 'keep', target: { employee_ids: string[] } | { all: true }) => {
    if (busy) return
    setBusy(true); setError('')
    try {
      await mdmApi.post('/staff/adp-decisions', { action, ...target })
      qc.invalidateQueries({ queryKey: ['staff'] })
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const decideAll = async (action: 'follow' | 'keep') => {
    const n = data?.adp_differs ?? 0
    if (!(await dlg.confirm({
      title: action === 'follow' ? 'Follow ADP for everyone' : 'Keep current for everyone',
      message: action === 'follow'
        ? `Write the latest ADP values onto all ${n} employee(s) that differ. Department changes create NC to-dos as usual.`
        : `Keep what UniOps has for all ${n} employee(s). They are not asked again until ADP changes those fields once more.`,
      confirmLabel: action === 'follow' ? 'Follow ADP' : 'Keep Current',
      tone: 'primary',
    }))) return
    decide(action, { all: true })
  }
  const editable = can('employee.edit')

  return (
    <StaffShell permission="employee.view">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-fg-muted" />
          <Input className="w-64 pl-9" placeholder="Search number, name or email"
            value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
        </div>
        {(Object.keys(FILTERS) as FilterKey[]).map((key) => (
          <Select key={key} value={filters[key] ?? ''}
            onChange={(e) => { setFilters((f) => ({ ...f, [key]: e.target.value })); setPage(1) }}>
            <option value="">{FILTERS[key].label}</option>
            {FILTERS[key].options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select>
        ))}
        <Select value={departmentId} onChange={(e) => { setDepartmentId(e.target.value); setPage(1) }}>
          <option value="">All departments</option>
          {depts?.items.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </Select>
        <label className="flex items-center gap-1.5 text-sm text-fg-secondary">
          <Checkbox checked={differs} onChange={(e) => { setDiffers(e.target.checked); setPage(1) }} />
          User differs
        </label>
        <label className="flex items-center gap-1.5 text-sm text-fg-secondary">
          <Checkbox checked={adpDiffers} onChange={(e) => { setAdpDiffers(e.target.checked); setPage(1) }} />
          Differs from ADP
        </label>
        <label className="flex items-center gap-1.5 text-sm text-fg-secondary">
          <Checkbox checked={needsDepartment} onChange={(e) => { setNeedsDepartment(e.target.checked); setPage(1) }} />
          Needs department
        </label>
        <div className="ml-auto flex gap-2">
          {admin && picked.length > 0 && (
            <Button size="md" disabled={busy} onClick={bulkCreate}>
              <UserPlus className="h-4 w-4" />Create {picked.length} user{picked.length > 1 ? 's' : ''}
            </Button>
          )}
          <Button variant="secondary" size="md" onClick={() => mdmDownload(`/staff/export?${filterQs}`, 'staff.xlsx')}>
            <Download className="h-4 w-4" />Export
          </Button>
          {can('employee.edit') && (
            <Button size="md" onClick={() => setAdding(true)}>
              <Plus className="h-4 w-4" />Add Non-ADP
            </Button>
          )}
        </div>
      </div>

      {error && <p className="mb-3 text-sm text-danger-fg">{error}</p>}

      {(data?.adp_differs ?? 0) > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-info-line bg-info-soft px-4 py-2.5 text-sm text-info-fg">
          <span>
            <b>{data!.adp_differs}</b> employee(s) differ from the latest ADP roster. Nothing was changed: choose
            <b> Follow ADP</b> or <b>Keep Current</b> for each.
          </span>
          {!adpDiffers && <Button variant="link" className="text-sm" onClick={() => { setAdpDiffers(true); setPage(1) }}>Show them</Button>}
          {editable && (
            <span className="ml-auto flex gap-2">
              <Button size="sm" disabled={busy} onClick={() => decideAll('follow')}>Follow ADP for all</Button>
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => decideAll('keep')}>Keep Current for all</Button>
            </span>
          )}
        </div>
      )}

      {bulk && (
        <div className="mb-4 rounded-lg border border-warning-line bg-warning-soft px-4 py-3 text-sm text-warning-fg">
          <div className="flex items-center justify-between font-medium">
            <span>{bulk.created.length} user(s) created, {bulk.errors.length} failed. Temporary passwords are shown only once.</span>
            <Button variant="subtle" size="icon-xs" onClick={() => setBulk(null)} aria-label="Dismiss"><X className="h-4 w-4" /></Button>
          </div>
          <ul className="mt-2 space-y-0.5 font-mono text-xs">
            {bulk.created.map((c) => <li key={c.email}>{c.email} — {c.temp_password}</li>)}
            {bulk.errors.map((e) => <li key={e.erp_person_code} className="text-danger-fg">{e.erp_person_code}: {e.reason}</li>)}
          </ul>
        </div>
      )}

      <div className="rounded-xl border border-line bg-surface overflow-hidden">
        <div className="max-h-[calc(100vh-300px)] overflow-auto">
          <Table>
            <THead>
              <Tr>
                {admin && <Th />}
                <SortTh label="No." sortKey="employee_number" sort={sort} onSort={onSort} />
                <SortTh label="Name" sortKey="display_name" sort={sort} onSort={onSort} />
                <SortTh label="Email" sortKey="work_email" sort={sort} onSort={onSort} />
                <SortTh label="Job Title" sortKey="job_title" sort={sort} onSort={onSort} />
                <SortTh label="ADP Department" sortKey="adp_department" sort={sort} onSort={onSort} />
                <SortTh label="Department" sortKey="department" sort={sort} onSort={onSort} />
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} />
                <SortTh label="Source" sortKey="source" sort={sort} onSort={onSort} />
                <SortTh label="Hire Date" sortKey="hire_date" sort={sort} onSort={onSort} />
                <SortTh label="Pay Type" sortKey="pay_type" sort={sort} onSort={onSort} />
                <SortTh label="Manager" sortKey="is_manager" sort={sort} onSort={onSort} />
                <SortTh label="User" sortKey="user" sort={sort} onSort={onSort} />
                <SortTh label="NC" sortKey="nc_state" sort={sort} onSort={onSort} />
              </Tr>
            </THead>
            <TBody>
              {data?.items.map((r) => (
                <Fragment key={r.id}>
                <Tr className={r.adp_pending ? 'bg-info-soft/40 hover:bg-surface-subtle' : 'hover:bg-surface-subtle'}>
                  {admin && (
                    <Td>
                      {canCreate(r) && <Checkbox aria-label={`Select ${r.display_name}`}
                        checked={!!selected[r.id]} onChange={() => toggle(r)} />}
                    </Td>
                  )}
                  <Td className="font-mono text-xs">{r.employee_number}</Td>
                  <Td><Link className="text-primary-700 hover:underline" to={`/mdm/staff/${r.id}`}>{r.display_name}</Link></Td>
                  <Td>{r.work_email ?? '—'}</Td>
                  <Td>{r.job_title ?? '—'}</Td>
                  <Td>{r.adp_department ?? '—'}</Td>
                  <Td>
                    {r.department_name ?? (r.needs_department ? <span className="text-warning-fg">Needs department</span> : '—')}
                  </Td>
                  <Td><StatusPill status={r.status} /></Td>
                  <Td>{SOURCE_LABEL[r.source]}</Td>
                  <Td className="whitespace-nowrap">{r.hire_date ? formatDate(r.hire_date) : '—'}</Td>
                  <Td>{r.pay_type ? PAY_TYPE_LABEL[r.pay_type] : '—'}</Td>
                  <Td>{r.is_manager ? 'Yes' : '—'}</Td>
                  <Td>
                    {r.user ? (r.user.is_active ? 'Yes' : 'Inactive')
                      : canCreate(r) ? (
                        <Button variant="link" className="text-xs"
                          onClick={() => navigate(`/mdm/users?from_employee=${r.id}`)}>Create user</Button>
                      ) : '—'}
                  </Td>
                  <Td title={NC_STATE_LABEL[r.nc_state]}>
                    <NcPill state={r.nc_state} />
                    {r.open_todos > 0 && <span className="ml-1 text-xs text-warning-fg">{r.open_todos} to-do</span>}
                  </Td>
                </Tr>
                {r.adp_pending && (
                  // IMP-016: under the person's row, so it is seen without scrolling the wide table sideways.
                  <Tr className="bg-info-soft/40">
                    <Td colSpan={admin ? 14 : 13} className="pt-0">
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                        {editable && (
                          <span className="flex gap-1.5">
                            <Button size="xs" disabled={busy} onClick={() => decide('follow', { employee_ids: [r.id] })}>Follow ADP</Button>
                            <Button size="xs" variant="secondary" disabled={busy} onClick={() => decide('keep', { employee_ids: [r.id] })}>Keep Current</Button>
                          </span>
                        )}
                        <span className="font-medium text-info-fg">ADP differs:</span>
                        {r.adp_pending.map((d) => (
                          <span key={d.field}>
                            <span className="text-fg-muted">{FIELD_LABEL[d.field] ?? d.field}: </span>
                            <span className="line-through">{fieldValue(d.field, d.current)}</span>
                            {' → '}<span className="font-medium text-fg">{fieldValue(d.field, d.adp)}</span>
                          </span>
                        ))}
                      </div>
                    </Td>
                  </Tr>
                )}
                </Fragment>
              ))}
            </TBody>
          </Table>
          {!isLoading && !data?.items.length && (
            <div className="py-10 text-center text-sm text-fg-muted">
              No employees found. Employees appear here after the first ADP import.
            </div>
          )}
        </div>
        <Pagination page={page} pageSize={pageSize} total={data?.total ?? 0}
          onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      </div>

      {dlg.dialog}
      {adding && <AddEmployee departments={depts?.items ?? []} onClose={() => setAdding(false)}
        onSaved={(id) => { qc.invalidateQueries({ queryKey: ['staff'] }); navigate(`/mdm/staff/${id}`) }} />}
    </StaffShell>
  )
}

function AddEmployee({ departments, onClose, onSaved }: {
  departments: Department[]; onClose: () => void; onSaved: (id: string) => void
}) {
  const [form, setForm] = useState({ employee_number: '', display_name: '', work_email: '', job_title: '', department_id: '', hire_date: '', pay_type: '' })
  const [manager, setManager] = useState(false)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true); setError('')
    try {
      const r = await mdmApi.post<{ id: string }>('/staff', {
        employee_number: form.employee_number.trim(), display_name: form.display_name,
        work_email: form.work_email || null, job_title: form.job_title || null,
        department_id: form.department_id || null,
        hire_date: form.hire_date || null, pay_type: form.pay_type || null, is_manager: manager,
      })
      onSaved(r.id)
    } catch (err) { setError((err as Error).message) } finally { setSaving(false) }
  }

  return (
    // Closes only by Cancel / the close button (no Escape, no backdrop), as before.
    <Modal open onClose={onClose} closeOnEscape={false} size="full" className="max-w-md"
      title="Add Non-ADP Employee"
      description="For people paid outside ADP, such as head-office staff. Everyone else comes from the ADP import.">
      <form onSubmit={save} className="space-y-3">
        <Input className="w-full" placeholder="Employee number (six digits)" pattern="\d{6}" required
          value={form.employee_number} onChange={set('employee_number')} />
        <Input className="w-full" placeholder="Name" required value={form.display_name} onChange={set('display_name')} />
        <Input className="w-full" type="email" placeholder="Work email" value={form.work_email} onChange={set('work_email')} />
        <Input className="w-full" placeholder="Job title" value={form.job_title} onChange={set('job_title')} />
        <Select className="w-full" value={form.department_id} onChange={set('department_id')}>
          <option value="">Department</option>
          {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </Select>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs text-fg-muted">Hire date
            <Input className="mt-1 w-full" type="date" value={form.hire_date} onChange={set('hire_date')} />
          </label>
          <label className="text-xs text-fg-muted">Pay type
            <Select className="mt-1 w-full" value={form.pay_type} onChange={set('pay_type')}>
              <option value="">Not set</option>
              <option value="hourly">Hourly</option>
              <option value="salaried">Salaried</option>
            </Select>
          </label>
        </div>
        <label className="flex items-center gap-1.5 text-sm text-fg-secondary">
          <Checkbox checked={manager} onChange={(e) => setManager(e.target.checked)} />
          Manager (gets the manager review form)
        </label>
        {error && <p className="text-sm text-danger-fg">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="md" type="button" onClick={onClose}>Cancel</Button>
          <Button size="md" type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </form>
    </Modal>
  )
}
