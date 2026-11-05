import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Plus, Search, UserPlus, X } from 'lucide-react'
import { Pagination, SortTh, sortParams, useSort } from '@uniops/shell'
import { epmsApi, mdmApi, mdmDownload } from '@/lib/api'
import {
  INPUT, NC_STATE_LABEL, NcPill, PRIMARY_BTN, SECONDARY_BTN, SOURCE_LABEL, StaffShell, StatusPill, TD, TH,
  useStaffPerm, type Department, type StaffRow,
} from './shared'

type SortKey = 'employee_number' | 'display_name' | 'work_email' | 'job_title' | 'adp_department' | 'department' | 'status' | 'source' | 'user' | 'nc_state'

interface BulkResult {
  created: { email: string; full_name: string; temp_password: string }[]
  errors: { erp_person_code: string; reason: string }[]
}

const FILTERS = {
  status: { label: 'All statuses', options: [['active', 'Active'], ['on_leave', 'On Leave'], ['left', 'Left']] },
  source: { label: 'All sources', options: [['adp', 'ADP'], ['non_adp', 'Non-ADP']] },
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
  const filterQs = params.toString()
  const qs = new URLSearchParams(params)
  qs.set('page', String(page))
  qs.set('page_size', String(pageSize))
  for (const [k, v] of Object.entries(sortParams(sort))) qs.set(k, String(v))

  const { data, isLoading } = useQuery<{ items: StaffRow[]; total: number }>({
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

  return (
    <StaffShell permission="employee.view">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-neutral-400" />
          <input className={`${INPUT} w-64 pl-9`} placeholder="Search number, name or email"
            value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
        </div>
        {(Object.keys(FILTERS) as FilterKey[]).map((key) => (
          <select key={key} className={INPUT} value={filters[key] ?? ''}
            onChange={(e) => { setFilters((f) => ({ ...f, [key]: e.target.value })); setPage(1) }}>
            <option value="">{FILTERS[key].label}</option>
            {FILTERS[key].options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        ))}
        <select className={INPUT} value={departmentId} onChange={(e) => { setDepartmentId(e.target.value); setPage(1) }}>
          <option value="">All departments</option>
          {depts?.items.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-sm text-neutral-600">
          <input type="checkbox" checked={differs} onChange={(e) => { setDiffers(e.target.checked); setPage(1) }} />
          User differs
        </label>
        <label className="flex items-center gap-1.5 text-sm text-neutral-600">
          <input type="checkbox" checked={needsDepartment} onChange={(e) => { setNeedsDepartment(e.target.checked); setPage(1) }} />
          Needs department
        </label>
        <div className="ml-auto flex gap-2">
          {admin && picked.length > 0 && (
            <button className={PRIMARY_BTN} disabled={busy} onClick={bulkCreate}>
              <UserPlus className="h-4 w-4" />Create {picked.length} user{picked.length > 1 ? 's' : ''}
            </button>
          )}
          <button className={SECONDARY_BTN} onClick={() => mdmDownload(`/staff/export?${filterQs}`, 'staff.xlsx')}>
            <Download className="h-4 w-4" />Export
          </button>
          {can('employee.edit') && (
            <button className={PRIMARY_BTN} onClick={() => setAdding(true)}>
              <Plus className="h-4 w-4" />Add Non-ADP
            </button>
          )}
        </div>
      </div>

      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}

      {bulk && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="flex items-center justify-between font-medium">
            <span>{bulk.created.length} user(s) created, {bulk.errors.length} failed. Temporary passwords are shown only once.</span>
            <button onClick={() => setBulk(null)} aria-label="Dismiss"><X className="h-4 w-4" /></button>
          </div>
          <ul className="mt-2 space-y-0.5 font-mono text-xs">
            {bulk.created.map((c) => <li key={c.email}>{c.email} — {c.temp_password}</li>)}
            {bulk.errors.map((e) => <li key={e.erp_person_code} className="text-red-700">{e.erp_person_code}: {e.reason}</li>)}
          </ul>
        </div>
      )}

      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <div className="max-h-[calc(100vh-300px)] overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                {admin && <th className={TH} />}
                <SortTh label="No." sortKey="employee_number" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Name" sortKey="display_name" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Email" sortKey="work_email" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Job Title" sortKey="job_title" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="ADP Department" sortKey="adp_department" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Department" sortKey="department" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Source" sortKey="source" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="User" sortKey="user" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="NC" sortKey="nc_state" sort={sort} onSort={onSort} className={TH} />
              </tr>
            </thead>
            <tbody>
              {data?.items.map((r) => (
                <tr key={r.id} className="border-t border-neutral-100 hover:bg-neutral-50">
                  {admin && (
                    <td className="px-4">
                      {canCreate(r) && <input type="checkbox" aria-label={`Select ${r.display_name}`}
                        checked={!!selected[r.id]} onChange={() => toggle(r)} />}
                    </td>
                  )}
                  <td className={`${TD} font-mono text-xs`}>{r.employee_number}</td>
                  <td className={TD}><Link className="text-[#085E5E] hover:underline" to={`/mdm/staff/${r.id}`}>{r.display_name}</Link></td>
                  <td className={TD}>{r.work_email ?? '—'}</td>
                  <td className={TD}>{r.job_title ?? '—'}</td>
                  <td className={TD}>{r.adp_department ?? '—'}</td>
                  <td className={TD}>
                    {r.department_name ?? (r.needs_department ? <span className="text-amber-700">Needs department</span> : '—')}
                  </td>
                  <td className={TD}><StatusPill status={r.status} /></td>
                  <td className={TD}>{SOURCE_LABEL[r.source]}</td>
                  <td className={TD}>
                    {r.user ? (r.user.is_active ? 'Yes' : 'Inactive')
                      : canCreate(r) ? (
                        <button className="text-xs font-medium text-[#085E5E] hover:underline"
                          onClick={() => navigate(`/mdm/users?from_employee=${r.id}`)}>Create user</button>
                      ) : '—'}
                  </td>
                  <td className={TD} title={NC_STATE_LABEL[r.nc_state]}>
                    <NcPill state={r.nc_state} />
                    {r.open_todos > 0 && <span className="ml-1 text-xs text-amber-700">{r.open_todos} to-do</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!isLoading && !data?.items.length && (
            <div className="py-10 text-center text-sm text-neutral-400">
              No employees found. Employees appear here after the first ADP import.
            </div>
          )}
        </div>
        <Pagination page={page} pageSize={pageSize} total={data?.total ?? 0}
          onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      </div>

      {adding && <AddEmployee departments={depts?.items ?? []} onClose={() => setAdding(false)}
        onSaved={(id) => { qc.invalidateQueries({ queryKey: ['staff'] }); navigate(`/mdm/staff/${id}`) }} />}
    </StaffShell>
  )
}

function AddEmployee({ departments, onClose, onSaved }: {
  departments: Department[]; onClose: () => void; onSaved: (id: string) => void
}) {
  const [form, setForm] = useState({ employee_number: '', display_name: '', work_email: '', job_title: '', department_id: '' })
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
      })
      onSaved(r.id)
    } catch (err) { setError((err as Error).message) } finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <form onSubmit={save} className="w-full max-w-md space-y-3 rounded-xl bg-white p-5 shadow-xl">
        <h3 className="font-semibold text-neutral-900">Add Non-ADP Employee</h3>
        <p className="text-xs text-neutral-500">For people paid outside ADP, such as head-office staff. Everyone else comes from the ADP import.</p>
        <input className={`${INPUT} w-full`} placeholder="Employee number (six digits)" pattern="\d{6}" required
          value={form.employee_number} onChange={set('employee_number')} />
        <input className={`${INPUT} w-full`} placeholder="Name" required value={form.display_name} onChange={set('display_name')} />
        <input className={`${INPUT} w-full`} type="email" placeholder="Work email" value={form.work_email} onChange={set('work_email')} />
        <input className={`${INPUT} w-full`} placeholder="Job title" value={form.job_title} onChange={set('job_title')} />
        <select className={`${INPUT} w-full`} value={form.department_id} onChange={set('department_id')}>
          <option value="">Department</option>
          {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className={SECONDARY_BTN} onClick={onClose}>Cancel</button>
          <button type="submit" className={PRIMARY_BTN} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </div>
  )
}
