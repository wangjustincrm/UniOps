import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, RefreshCw } from 'lucide-react'
import { Button, Checkbox, Input, Pagination, Segmented, Select, SortTh, TBody, THead, Table, Td, Th, Tr, sortParams, useSort, Card } from '@uniops/shell'
import { mdmApi, mdmDownload } from '@/lib/api'
import { formatDate, cn } from '@/lib/utils'
import {
  NC_STATE_LABEL, STATUS_LABEL, StaffShell, TODO_LABEL, targetText, useStaffPerm, type NcTodoRow,
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
        <Segmented size="md" items={[{ key: 'todos' as const, label: 'To-dos' }, { key: 'report' as const, label: 'Reconciliation' }]}
          value={view} onChange={setView} />
        <span className="text-xs text-fg-muted">
          {report?.ran_at ? `NC last read ${formatDate(report.ran_at)}` : 'NC has not been read yet'}
        </span>
        <div className="ml-auto flex gap-2">
          {closeable && (
            <Button variant="secondary" size="md" disabled={busy} onClick={() => run(() => mdmApi.post('/staff/nc/check'))}>
              <RefreshCw className="h-4 w-4" />{busy ? 'Reading NC…' : 'Check NC now'}
            </Button>
          )}
          {view === 'todos' && (
            <Button variant="secondary" size="md" onClick={() => mdmDownload(`/staff/nc-todos/export?${filterQs}`, 'nc-todo.xlsx')}>
              <Download className="h-4 w-4" />Export
            </Button>
          )}
        </div>
      </div>

      {error && <p className="mb-3 text-sm text-danger-fg">{error}</p>}

      {view === 'todos' ? (
        <>
          {!!report?.not_in_nc.length && (
            <p className="mb-3 rounded-lg border border-line bg-surface-subtle px-3 py-2 text-sm text-fg-secondary">
              {report.not_in_nc.length} employees are not in NC. For information only: add them in NC if they need to be there.{' '}
              <Button variant="link" onClick={() => setView('report')}>View list</Button>
            </p>
          )}
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Input className="w-64" placeholder="Search number or name" value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
            <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
              <option value="open">Open</option><option value="closed">Closed</option><option value="">All</option>
            </Select>
            <Select value={type} onChange={(e) => { setType(e.target.value); setPage(1) }}>
              <option value="">All types</option>
              {Object.entries(TODO_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </Select>
            {closeable && picked.size > 0 && (
              <div className="ml-auto flex items-center gap-2">
                <Input className="min-w-64" placeholder="Reason for closing by hand" value={reason} onChange={(e) => setReason(e.target.value)} />
                <Button size="md" disabled={busy || !reason.trim()} onClick={() => run(async () => {
                  await mdmApi.post('/staff/nc-todos/close', { ids: [...picked], reason })
                  setPicked(new Set()); setReason('')
                })}>Close {picked.size}</Button>
              </div>
            )}
          </div>
          <Card className="overflow-hidden">
            <div className="max-h-[calc(100vh-340px)] overflow-auto">
              <Table>
                <THead><Tr>
                  {closeable && <Th />}
                  <SortTh label="No." sortKey="employee_number" sort={sort} onSort={onSort} />
                  <SortTh label="Name" sortKey="display_name" sort={sort} onSort={onSort} />
                  <SortTh label="To-do" sortKey="type" sort={sort} onSort={onSort} />
                  <Th>What to do in NC</Th>
                  <SortTh label="Created" sortKey="created_at" sort={sort} onSort={onSort} />
                  <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} />
                  <SortTh label="Closed" sortKey="closed_at" sort={sort} onSort={onSort} />
                </Tr></THead>
                <TBody>
                  {data?.items.map((t) => (
                    <Tr key={t.id} className={cn('border-t border-line-subtle', t.overdue && 'bg-warning-soft')}>
                      {closeable && <Td>
                        {t.status === 'open' && <Checkbox aria-label={`Select ${t.display_name}`} checked={picked.has(t.id)} onChange={() => toggle(t.id)} />}
                      </Td>}
                      <Td className="font-mono text-xs">{t.employee_number}</Td>
                      <Td><Link className="text-primary-700 hover:underline" to={`/mdm/staff/${t.employee_id}`}>{t.display_name}</Link></Td>
                      <Td className="whitespace-nowrap">{TODO_LABEL[t.type] ?? t.type}</Td>
                      <Td>{targetText(t)}</Td>
                      <Td className="whitespace-nowrap">{formatDate(t.created_at)}{t.overdue && <span className="ml-1 text-xs font-medium text-warning-fg">overdue</span>}</Td>
                      <Td>{t.status === 'open' ? 'Open' : `Closed (${t.closed_how})`}</Td>
                      <Td title={t.close_reason ?? undefined}>{t.closed_at ? formatDate(t.closed_at) : '—'}{t.close_reason ? ` · ${t.close_reason}` : ''}</Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
              {!data?.items.length && <p className="py-8 text-center text-sm text-fg-muted">No to-dos.</p>}
            </div>
            <Pagination page={page} pageSize={pageSize} total={data?.total ?? 0}
              onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
          </Card>
        </>
      ) : (
        <div className="space-y-4">
          {report?.summary && (
            <p className="text-sm text-fg-secondary">
              {report.summary.employees} employees, {report.summary.nc_persons} active NC persons: {report.summary.in_step} in step,{' '}
              {report.differences.length} different, {report.not_in_nc.length} not in NC, {report.not_in_uniops.length} in NC only.
            </p>
          )}
          <Section title={`Differences (${report?.differences.length ?? 0})`}
            head={['No.', 'Name', 'Status', 'Difference', 'UniOps department', 'NC code', 'NC department']}
            rows={(report?.differences ?? []).map((d) => [
              d.employee_number, <Link key="n" className="text-primary-700 hover:underline" to={`/mdm/staff/${d.employee_id}`}>{d.display_name}</Link>,
              STATUS_LABEL[d.status], NC_STATE_LABEL[d.nc_state], d.department ? `${d.department_code} ${d.department}` : '—',
              d.nc_person_code ?? '—', d.nc_department_code ? `${d.nc_department_code} ${d.nc_department_name ?? ''}` : '—',
            ])} />
          <Section title={`Not in NC (${report?.not_in_nc.length ?? 0}) — add in NC if needed`}
            head={['No.', 'Name', 'Job title', 'UniOps department']}
            rows={(report?.not_in_nc ?? []).map((e) => [
              e.employee_number, <Link key="n" className="text-primary-700 hover:underline" to={`/mdm/staff/${e.employee_id}`}>{e.display_name}</Link>,
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
    <Card as="section" className="overflow-hidden">
      <h2 className="px-4 pt-4 text-sm font-semibold text-fg">{title}</h2>
      <div className="mt-2 max-h-96 overflow-auto">
        <Table>
          <THead><Tr>{head.map((h) => <Th key={h}>{h}</Th>)}</Tr></THead>
          <TBody>
            {rows.map((r, i) => <Tr key={i}>{r.map((c, j) => <Td key={j}>{c}</Td>)}</Tr>)}
          </TBody>
        </Table>
        {!rows.length && <p className="py-6 text-center text-sm text-fg-muted">Nothing here.</p>}
      </div>
    </Card>
  )
}
