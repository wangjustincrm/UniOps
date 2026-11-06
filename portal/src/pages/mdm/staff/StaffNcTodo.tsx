import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, RefreshCw } from 'lucide-react'
import { Pagination, SortTh, sortParams, useSort } from '@uniops/shell'
import { mdmApi, mdmDownload } from '@/lib/api'
import { formatDate, cn } from '@/lib/utils'
import {
  INPUT, NC_STATE_LABEL, PRIMARY_BTN, SECONDARY_BTN, STATUS_LABEL, StaffShell, TD, TH, TODO_LABEL,
  targetText, useStaffPerm, type NcTodoRow,
} from './shared'

type SortKey = 'employee_number' | 'display_name' | 'type' | 'status' | 'created_at' | 'closed_at'

interface Report {
  ran_at: string | null
  summary: Record<string, number> | null
  differences: {
    employee_id: string; employee_number: string; display_name: string; status: string; nc_state: string
    department: string | null; department_code: string | null; nc_person_code: string | null
    nc_department_code: string | null; nc_department_name: string | null
  }[]
  not_in_nc: { employee_id: string; employee_number: string; display_name: string; job_title: string | null; department: string | null; department_code: string | null }[]
  not_in_uniops: { pk: string; code: string; name: string | null; department_code: string | null; department_name: string | null }[]
}
interface UnlinkedUser { id: string; email: string; full_name: string; erp_person_code: string | null }

export default function StaffNcTodo() {
  const qc = useQueryClient()
  const { can } = useStaffPerm()
  const [view, setView] = useState<'todos' | 'report'>('todos')
  const [status, setStatus] = useState('open')
  const [type, setType] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [sort, onSort] = useSort<SortKey>(null, () => setPage(1))
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const params = new URLSearchParams()
  if (status) params.set('status', status)
  if (type) params.set('type', type)
  if (search) params.set('search', search)
  const filterQs = params.toString()
  const qs = new URLSearchParams(params)
  qs.set('page', String(page))
  qs.set('page_size', String(pageSize))
  for (const [k, v] of Object.entries(sortParams(sort))) qs.set(k, String(v))

  const { data } = useQuery<{ items: NcTodoRow[]; total: number }>({
    queryKey: ['staff-todos', qs.toString()], queryFn: () => mdmApi.get(`/staff/nc-todos?${qs}`),
    placeholderData: (prev) => prev,
  })
  const { data: report } = useQuery<Report>({
    queryKey: ['staff-nc-report'], queryFn: () => mdmApi.get('/staff/nc/report'),
  })
  const { data: unlinked } = useQuery<{ items: UnlinkedUser[] }>({
    queryKey: ['staff-unlinked'], queryFn: () => mdmApi.get('/staff/unlinked-users'), enabled: view === 'report',
  })

  const run = async (action: () => Promise<unknown>) => {
    if (busy) return
    setBusy(true); setError('')
    try {
      await action()
      for (const key of ['staff-todos', 'staff-nc-report', 'staff']) qc.invalidateQueries({ queryKey: [key] })
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const toggle = (id: string) => setPicked((s) => {
    const next = new Set(s)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })
  const closeable = can('employee.nc_todo')

  return (
    <StaffShell permission="employee.view">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-neutral-200 bg-white p-0.5">
          {(['todos', 'report'] as const).map((v) => (
            <button key={v} onClick={() => setView(v)}
              className={cn('rounded-md px-3 py-1.5 text-sm font-medium', view === v ? 'bg-[#085E5E] text-white' : 'text-neutral-600')}>
              {v === 'todos' ? 'To-dos' : 'Reconciliation'}
            </button>
          ))}
        </div>
        <span className="text-xs text-neutral-500">
          {report?.ran_at ? `NC last read ${formatDate(report.ran_at)}` : 'NC has not been read yet'}
        </span>
        <div className="ml-auto flex gap-2">
          {closeable && (
            <button className={SECONDARY_BTN} disabled={busy} onClick={() => run(() => mdmApi.post('/staff/nc/check'))}>
              <RefreshCw className="h-4 w-4" />{busy ? 'Reading NC…' : 'Check NC now'}
            </button>
          )}
          {view === 'todos' && (
            <button className={SECONDARY_BTN} onClick={() => mdmDownload(`/staff/nc-todos/export?${filterQs}`, 'nc-todo.xlsx')}>
              <Download className="h-4 w-4" />Export
            </button>
          )}
        </div>
      </div>

      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}

      {view === 'todos' ? (
        <>
          {!!report?.not_in_nc.length && (
            <p className="mb-3 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-600">
              {report.not_in_nc.length} employees are not in NC. For information only: add them in NC if they need to be there.{' '}
              <button className="font-medium text-[#085E5E] hover:underline" onClick={() => setView('report')}>View list</button>
            </p>
          )}
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <input className={`${INPUT} w-64`} placeholder="Search number or name" value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
            <select className={INPUT} value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
              <option value="open">Open</option><option value="closed">Closed</option><option value="">All</option>
            </select>
            <select className={INPUT} value={type} onChange={(e) => { setType(e.target.value); setPage(1) }}>
              <option value="">All types</option>
              {Object.entries(TODO_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            {closeable && picked.size > 0 && (
              <div className="ml-auto flex items-center gap-2">
                <input className={`${INPUT} min-w-64`} placeholder="Reason for closing by hand" value={reason} onChange={(e) => setReason(e.target.value)} />
                <button className={PRIMARY_BTN} disabled={busy || !reason.trim()} onClick={() => run(async () => {
                  await mdmApi.post('/staff/nc-todos/close', { ids: [...picked], reason })
                  setPicked(new Set()); setReason('')
                })}>Close {picked.size}</button>
              </div>
            )}
          </div>
          <div className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
            <div className="max-h-[calc(100vh-340px)] overflow-auto">
              <table className="w-full">
                <thead><tr>
                  {closeable && <th className={TH} />}
                  <SortTh label="No." sortKey="employee_number" sort={sort} onSort={onSort} className={TH} />
                  <SortTh label="Name" sortKey="display_name" sort={sort} onSort={onSort} className={TH} />
                  <SortTh label="To-do" sortKey="type" sort={sort} onSort={onSort} className={TH} />
                  <th className={TH}>What to do in NC</th>
                  <SortTh label="Created" sortKey="created_at" sort={sort} onSort={onSort} className={TH} />
                  <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className={TH} />
                  <SortTh label="Closed" sortKey="closed_at" sort={sort} onSort={onSort} className={TH} />
                </tr></thead>
                <tbody>
                  {data?.items.map((t) => (
                    <tr key={t.id} className={cn('border-t border-neutral-100', t.overdue && 'bg-amber-50')}>
                      {closeable && <td className="px-4">
                        {t.status === 'open' && <input type="checkbox" aria-label={`Select ${t.display_name}`} checked={picked.has(t.id)} onChange={() => toggle(t.id)} />}
                      </td>}
                      <td className={`${TD} font-mono text-xs`}>{t.employee_number}</td>
                      <td className={TD}><Link className="text-[#085E5E] hover:underline" to={`/mdm/staff/${t.employee_id}`}>{t.display_name}</Link></td>
                      <td className={`${TD} whitespace-nowrap`}>{TODO_LABEL[t.type] ?? t.type}</td>
                      <td className={TD}>{targetText(t)}</td>
                      <td className={`${TD} whitespace-nowrap`}>{formatDate(t.created_at)}{t.overdue && <span className="ml-1 text-xs font-medium text-amber-700">overdue</span>}</td>
                      <td className={TD}>{t.status === 'open' ? 'Open' : `Closed (${t.closed_how})`}</td>
                      <td className={TD} title={t.close_reason ?? undefined}>{t.closed_at ? formatDate(t.closed_at) : '—'}{t.close_reason ? ` · ${t.close_reason}` : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!data?.items.length && <p className="py-8 text-center text-sm text-neutral-400">No to-dos.</p>}
            </div>
            <Pagination page={page} pageSize={pageSize} total={data?.total ?? 0}
              onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
          </div>
        </>
      ) : (
        <div className="space-y-4">
          {report?.summary && (
            <p className="text-sm text-neutral-600">
              {report.summary.employees} employees, {report.summary.nc_persons} active NC persons: {report.summary.in_step} in step,{' '}
              {report.differences.length} different, {report.not_in_nc.length} not in NC, {report.not_in_uniops.length} in NC only.
            </p>
          )}
          <Section title={`Differences (${report?.differences.length ?? 0})`}
            head={['No.', 'Name', 'Status', 'Difference', 'UniOps department', 'NC code', 'NC department']}
            rows={(report?.differences ?? []).map((d) => [
              d.employee_number, <Link key="n" className="text-[#085E5E] hover:underline" to={`/mdm/staff/${d.employee_id}`}>{d.display_name}</Link>,
              STATUS_LABEL[d.status], NC_STATE_LABEL[d.nc_state], d.department ? `${d.department_code} ${d.department}` : '—',
              d.nc_person_code ?? '—', d.nc_department_code ? `${d.nc_department_code} ${d.nc_department_name ?? ''}` : '—',
            ])} />
          <Section title={`Not in NC (${report?.not_in_nc.length ?? 0}) — add in NC if needed`}
            head={['No.', 'Name', 'Job title', 'UniOps department']}
            rows={(report?.not_in_nc ?? []).map((e) => [
              e.employee_number, <Link key="n" className="text-[#085E5E] hover:underline" to={`/mdm/staff/${e.employee_id}`}>{e.display_name}</Link>,
              e.job_title ?? '—', e.department ? `${e.department_code} ${e.department}` : '—',
            ])} />
          <Section title={`In NC, not in UniOps (${report?.not_in_uniops.length ?? 0})`}
            head={['NC code', 'Name', 'NC department']}
            rows={(report?.not_in_uniops ?? []).map((p) => [p.code, p.name ?? '(no name)', `${p.department_code ?? ''} ${p.department_name ?? ''}`])} />
          <Section title={`Active users with no employee (${unlinked?.items.length ?? 0})`}
            head={['Name', 'Email', 'ERP code']}
            rows={(unlinked?.items ?? []).map((u) => [u.full_name, u.email, u.erp_person_code ?? '—'])} />
        </div>
      )}
    </StaffShell>
  )
}

function Section({ title, head, rows }: { title: string; head: string[]; rows: React.ReactNode[][] }) {
  return (
    <section className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
      <h2 className="px-4 pt-4 text-sm font-semibold text-neutral-900">{title}</h2>
      <div className="mt-2 max-h-96 overflow-auto">
        <table className="w-full">
          <thead><tr>{head.map((h) => <th key={h} className={TH}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => <tr key={i} className="border-t border-neutral-100">{r.map((c, j) => <td key={j} className={TD}>{c}</td>)}</tr>)}
          </tbody>
        </table>
        {!rows.length && <p className="py-6 text-center text-sm text-neutral-400">Nothing here.</p>}
      </div>
    </section>
  )
}
