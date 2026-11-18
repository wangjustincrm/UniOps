import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { epmsApi, mdmApi } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { useConfirmDialog } from '@/components/ConfirmDialog'
import { Checkbox, Input, Select, TBody, THead, Table, Td, Textarea, Th, Tr, Button } from '@uniops/shell'
import {
  NcPill, SOURCE_LABEL, StaffShell, StatusPill, TODO_LABEL, targetText, useStaffPerm, type Department, type NcTodoRow, type StaffRow,
} from './shared'

interface Difference { field: string; label: string; employee: string | null; user: string | null; value: unknown }
interface HistoryRow {
  field: string; old_value: string | null; new_value: string | null; reason: string | null
  from_import: boolean; by: string | null; at: string
}
interface Detail extends StaffRow {
  notes: string | null
  nc: { person_code: string | null; department_code: string | null; department_name: string | null; checked_at: string | null }
  todos: NcTodoRow[]
  differences: Difference[]
  history: HistoryRow[]
}

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="text-sm text-fg">{children || '—'}</dd>
    </div>
  )
}

export default function StaffDetail() {
  const { id } = useParams()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { admin, can } = useStaffPerm()
  const [error, setError] = useState('')
  const dlg = useConfirmDialog()
  const [busy, setBusy] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const [leftOn, setLeftOn] = useState('')
  const [deactivate, setDeactivate] = useState(true)
  const [correcting, setCorrecting] = useState(false)
  const [number, setNumber] = useState('')
  const [reason, setReason] = useState('')

  const { data: e } = useQuery<Detail>({ queryKey: ['staff', 'detail', id], queryFn: () => mdmApi.get(`/staff/${id}`) })
  const { data: depts } = useQuery<{ items: Department[] }>({
    queryKey: ['portal-departments'], queryFn: () => mdmApi.get('/departments'),
  })
  // Held here until Save, so nobody changes a record without knowing it.
  const [notes, setNotes] = useState<string | null>(null)
  const [departmentId, setDepartmentId] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const run = async (action: () => Promise<unknown>) => {
    if (busy) return
    setBusy(true); setError('')
    try {
      await action()
      qc.invalidateQueries({ queryKey: ['staff'] })
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  if (!e) return <StaffShell permission="employee.view"><p className="text-sm text-fg-muted">Loading…</p></StaffShell>

  const editable = can('employee.edit')
  const pending: { department_id?: string | null; notes?: string } = {}
  if (departmentId !== null && departmentId !== (e.department_id ?? '')) pending.department_id = departmentId || null
  if (notes !== null && notes !== (e.notes ?? '')) pending.notes = notes
  const changes = Object.keys(pending).length ? pending : null
  const applyDifference = async (d: Difference) => {
    // Asked in-page (was the browser's confirm).
    if (d.field === 'email' && !(await dlg.confirm({
      title: 'Change sign-in',
      message: `This changes ${e.display_name}'s sign-in to ${d.employee}. Continue?`,
      confirmLabel: 'Continue',
      tone: 'primary',
    }))) return
    run(() => epmsApi.patch(`/users/${e.user!.id}`, { [d.field]: d.value }))
  }
  const markLeft = () => run(async () => {
    await mdmApi.post(`/staff/${e.id}/leave`, { left_on: leftOn })
    // The existing deactivation also re-syncs their in-flight approvals (USR-003).
    if (deactivate && e.user?.is_active) await epmsApi.delete(`/users/${e.user.id}`)
    setLeaving(false)
  })

  return (
    <StaffShell permission="employee.view">
      <Link to="/mdm/staff" className="mb-3 inline-flex items-center gap-1 text-sm text-fg-muted hover:text-fg">
        <ArrowLeft className="h-4 w-4" />Staff
      </Link>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-fg">{e.display_name}</h1>
        <span className="font-mono text-sm text-fg-muted">{e.employee_number}</span>
        <StatusPill status={e.status} />
        <div className="ml-auto flex gap-2">
          {admin && !e.user && e.status !== 'left' && (
            <Button size="md" onClick={() => navigate(`/mdm/users?from_employee=${e.id}`)}>Create user</Button>
          )}
          {editable && e.status !== 'left' && <Button variant="secondary" size="md" onClick={() => setLeaving(true)}>Mark as Left</Button>}
          {editable && e.status === 'left' && (
            <Button variant="secondary" size="md" disabled={busy} onClick={() => run(() => mdmApi.post(`/staff/${e.id}/reinstate`))}>Reinstate</Button>
          )}
          {admin && <Button variant="secondary" size="md" onClick={() => setCorrecting(true)}>Correct number</Button>}
        </div>
      </div>

      {error && <p className="mb-3 text-sm text-danger-fg">{error}</p>}

      {leaving && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-surface p-3">
          <label className="text-sm text-fg-secondary">Left on <Input type="date" className="ml-1" value={leftOn} onChange={(ev) => setLeftOn(ev.target.value)} /></label>
          {e.user?.is_active && (
            <label className="flex items-center gap-1.5 text-sm text-fg-secondary">
              <Checkbox checked={deactivate} onChange={(ev) => setDeactivate(ev.target.checked)} />
              Also deactivate their user ({e.user.email})
            </label>
          )}
          <Button size="md" disabled={!leftOn || busy} onClick={markLeft}>Confirm</Button>
          <Button variant="secondary" size="md" onClick={() => setLeaving(false)}>Cancel</Button>
        </div>
      )}

      {correcting && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-surface p-3">
          <Input placeholder="New number (six digits)" value={number} onChange={(ev) => setNumber(ev.target.value)} />
          <Input className="min-w-64" placeholder="Reason" value={reason} onChange={(ev) => setReason(ev.target.value)} />
          <Button size="md" disabled={!/^\d{6}$/.test(number) || !reason.trim() || busy}
            onClick={() => run(async () => {
              await mdmApi.post(`/staff/${e.id}/correct-number`, { new_number: number, reason })
              setCorrecting(false)
            })}>Save</Button>
          <Button variant="secondary" size="md" onClick={() => setCorrecting(false)}>Cancel</Button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-line bg-surface p-4">
          <h2 className="mb-3 text-sm font-semibold text-fg">Employee</h2>
          <dl className="grid grid-cols-2 gap-3">
            <Line label="Source">{SOURCE_LABEL[e.source]}</Line>
            <Line label="Payroll name">{e.payroll_name}</Line>
            <Line label="Work email">{e.work_email}</Line>
            <Line label="Job title">{e.job_title}</Line>
            <Line label="ADP department">{e.adp_department}</Line>
            <Line label="Left on">{e.left_on && formatDate(e.left_on)}</Line>
            <div className="col-span-2">
              <dt className="text-xs text-fg-muted">UniOps department</dt>
              {editable ? (
                <Select className="mt-1 w-full" value={departmentId ?? e.department_id ?? ''} disabled={busy}
                  onChange={(ev) => { setDepartmentId(ev.target.value); setSaved(false) }}>
                  <option value="">Not set</option>
                  {depts?.items.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </Select>
              ) : <dd className="text-sm text-fg">{e.department_name ?? '—'}</dd>}
            </div>
            <div className="col-span-2">
              <dt className="text-xs text-fg-muted">Notes</dt>
              {editable ? (
                <Textarea className="mt-1 w-full" rows={2} value={notes ?? e.notes ?? ''}
                  onChange={(ev) => { setNotes(ev.target.value); setSaved(false) }} />
              ) : <dd className="text-sm text-fg">{e.notes ?? '—'}</dd>}
            </div>
          </dl>
          {editable && (changes || saved) && (
            <div className="mt-3 flex items-center gap-2">
              {changes ? (
                <>
                  <Button size="md" disabled={busy} onClick={() => run(async () => {
                    await mdmApi.patch(`/staff/${e.id}`, changes)
                    setDepartmentId(null); setNotes(null); setSaved(true)
                  })}>{busy ? 'Saving…' : 'Save'}</Button>
                  <Button variant="secondary" size="md" disabled={busy} onClick={() => { setDepartmentId(null); setNotes(null) }}>Cancel</Button>
                  <span className="text-xs text-warning-fg">Not saved yet</span>
                </>
              ) : <span className="text-sm text-primary-700">Saved</span>}
            </div>
          )}
          {e.source === 'adp' && <p className="mt-3 text-xs text-fg-muted">Name, email, job title, ADP department and status come from ADP and are overwritten by each import.</p>}
        </section>

        <div className="space-y-4">
          <section className="rounded-xl border border-line bg-surface p-4">
            <h2 className="mb-3 text-sm font-semibold text-fg">User</h2>
            {e.user ? (
              <>
                <p className="text-sm text-fg">{e.user.full_name} · {e.user.email} {!e.user.is_active && '(inactive)'}</p>
                {e.differences.length > 0 && (
                  <Table density="compact" className="mt-3">
                    <THead><Tr className="text-left text-xs text-fg-muted"><Th>Field</Th><Th>Employee</Th><Th>User</Th><Th /></Tr></THead>
                    <TBody>
                      {e.differences.map((d) => (
                        <Tr key={d.field}>
                          <Td>{d.label}</Td>
                          <Td>{d.employee ?? '—'}</Td>
                          <Td>{d.user ?? '—'}</Td>
                          <Td align="right">
                            {admin && <Button variant="link" className="text-xs" disabled={busy}
                              onClick={() => applyDifference(d)}>Apply to user</Button>}
                          </Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                )}
              </>
            ) : <p className="text-sm text-fg-muted">No user account.</p>}
          </section>

          <section className="rounded-xl border border-line bg-surface p-4">
            <h2 className="mb-3 text-sm font-semibold text-fg">NC</h2>
            <dl className="grid grid-cols-2 gap-3">
              <Line label="State"><NcPill state={e.nc_state} /></Line>
              <Line label="Person code">{e.nc.person_code}</Line>
              <Line label="NC department">{e.nc.department_code && `${e.nc.department_code} ${e.nc.department_name ?? ''}`}</Line>
              <Line label="Checked">{e.nc.checked_at && formatDate(e.nc.checked_at)}</Line>
            </dl>
            {e.todos.length > 0 && (
              <ul className="mt-3 space-y-1 text-sm">
                {e.todos.map((t) => <li key={t.id}><span className="font-medium">{TODO_LABEL[t.type]}</span> <span className="text-fg-muted">{targetText(t)}</span></li>)}
              </ul>
            )}
          </section>
        </div>
      </div>

      <section className="mt-4 overflow-hidden rounded-xl border border-line bg-surface">
        <h2 className="px-4 pt-4 text-sm font-semibold text-fg">History</h2>
        <div className="mt-2 max-h-80 overflow-auto">
          <Table>
            <THead><Tr><Th>When</Th><Th>Field</Th><Th>Old</Th><Th>New</Th><Th>By</Th></Tr></THead>
            <TBody>
              {e.history.map((h, i) => (
                <Tr key={i}>
                  <Td>{formatDate(h.at)}</Td>
                  <Td>{h.field}{h.reason ? ` (${h.reason})` : ''}</Td>
                  <Td>{h.old_value ?? '—'}</Td>
                  <Td>{h.new_value ?? '—'}</Td>
                  <Td>{h.from_import ? 'ADP import' : h.by ?? '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
      </section>
      {dlg.dialog}
    </StaffShell>
  )
}
