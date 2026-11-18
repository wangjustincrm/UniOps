import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Pencil } from 'lucide-react'
import { epmsApi, mdmApi } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { useConfirmDialog } from '@/components/ConfirmDialog'
import { Checkbox, Input, Select, TBody, THead, Table, Td, Textarea, Th, Tr, Button } from '@uniops/shell'
import {
  FIELD_LABEL, NcPill, PAY_TYPE_LABEL, SOURCE_LABEL, STATUS_LABEL, StaffShell, fieldValue, StatusPill, TODO_LABEL, targetText, useStaffPerm, type Department, type NcTodoRow, type StaffRow,
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

function Field({ label, wide, children }: { label: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <label className={wide ? 'col-span-2 block' : 'block'}>
      <span className="mb-1 block text-xs text-fg-muted">{label}</span>
      {children}
    </label>
  )
}

/** Every editable field as the form holds it: text, '' for empty. */
interface Draft {
  display_name: string; payroll_name: string; work_email: string; job_title: string; adp_department: string
  department_id: string; status: 'active' | 'on_leave'; left_on: string; hire_date: string; pay_type: string
  is_manager: boolean; notes: string
}

function toDraft(e: Detail): Draft {
  return {
    display_name: e.display_name, payroll_name: e.payroll_name, work_email: e.work_email ?? '',
    job_title: e.job_title ?? '', adp_department: e.adp_department ?? '', department_id: e.department_id ?? '',
    status: e.status === 'on_leave' ? 'on_leave' : 'active', left_on: e.left_on ?? '', hire_date: e.hire_date ?? '',
    pay_type: e.pay_type ?? '', is_manager: e.is_manager, notes: e.notes ?? '',
  }
}

/** Only what changed goes to the server, so the history records only real changes. */
function diff(draft: Draft, e: Detail): Record<string, unknown> | null {
  const before = toDraft(e)
  const out: Record<string, unknown> = {}
  for (const key of Object.keys(draft) as (keyof Draft)[]) {
    if (draft[key] === before[key]) continue
    if (key === 'status' && e.status === 'left') continue
    if (key === 'left_on' && e.status !== 'left') continue
    const value = draft[key]
    out[key] = typeof value === 'string' && key !== 'notes' && key !== 'status' ? (value.trim() || null) : value
  }
  return Object.keys(out).length ? out : null
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
  // Edit mode holds every field here until Save, so nobody changes a record without knowing it.
  const [draft, setDraft] = useState<Draft | null>(null)
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
  const changes = draft ? diff(draft, e) : null
  const set = <K extends keyof Draft>(k: K) => (v: Draft[K]) => { setDraft((d) => d && { ...d, [k]: v }); setSaved(false) }
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

      {e.adp_pending && (
        <section className="mb-4 rounded-xl border border-info-line bg-info-soft p-4">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-info-fg">
              The ADP roster{e.adp_pending_since ? ` of ${formatDate(e.adp_pending_since)}` : ''} differs. Nothing has been changed yet.
            </h2>
            {editable && (
              <span className="ml-auto flex gap-2">
                <Button size="sm" disabled={busy || !!draft}
                  onClick={() => run(() => mdmApi.post('/staff/adp-decisions', { action: 'follow', employee_ids: [e.id] }))}>Follow ADP</Button>
                <Button size="sm" variant="secondary" disabled={busy || !!draft}
                  onClick={() => run(() => mdmApi.post('/staff/adp-decisions', { action: 'keep', employee_ids: [e.id] }))}>Keep Current</Button>
              </span>
            )}
          </div>
          <Table density="compact">
            <THead><Tr className="text-left text-xs text-fg-muted"><Th>Field</Th><Th>Now in UniOps</Th><Th>In ADP</Th></Tr></THead>
            <TBody>
              {e.adp_pending.map((d) => (
                <Tr key={d.field}>
                  <Td>{FIELD_LABEL[d.field] ?? d.field}</Td>
                  <Td>{fieldValue(d.field, d.current)}</Td>
                  <Td className="font-medium">{fieldValue(d.field, d.adp)}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
          <p className="mt-2 text-xs text-info-fg">
            Follow ADP writes the ADP values. Keep Current leaves the record as it is and is not asked again until ADP changes these fields once more.
          </p>
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-line bg-surface p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-fg">Employee</h2>
            {editable && !draft && (
              <Button variant="secondary" size="sm" disabled={busy}
                onClick={() => { setDraft(toDraft(e)); setSaved(false) }}>
                <Pencil className="h-3.5 w-3.5" />Edit
              </Button>
            )}
          </div>
          {draft ? (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Name"><Input className="w-full" value={draft.display_name} disabled={busy} onChange={(ev) => set('display_name')(ev.target.value)} /></Field>
              <Field label="Payroll name"><Input className="w-full" value={draft.payroll_name} disabled={busy} onChange={(ev) => set('payroll_name')(ev.target.value)} /></Field>
              <Field label="Work email"><Input className="w-full" type="email" value={draft.work_email} disabled={busy} onChange={(ev) => set('work_email')(ev.target.value)} /></Field>
              <Field label="Job title"><Input className="w-full" value={draft.job_title} disabled={busy} onChange={(ev) => set('job_title')(ev.target.value)} /></Field>
              <Field label="ADP department"><Input className="w-full" value={draft.adp_department} disabled={busy} onChange={(ev) => set('adp_department')(ev.target.value)} /></Field>
              <Field label="UniOps department">
                <Select className="w-full" value={draft.department_id} disabled={busy} onChange={(ev) => set('department_id')(ev.target.value)}>
                  <option value="">Not set</option>
                  {depts?.items.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </Select>
              </Field>
              {e.status === 'left' ? (
                <Field label="Left on"><Input className="w-full" type="date" value={draft.left_on} disabled={busy} onChange={(ev) => set('left_on')(ev.target.value)} /></Field>
              ) : (
                <Field label="Status">
                  <Select className="w-full" value={draft.status} disabled={busy} onChange={(ev) => set('status')(ev.target.value as Draft['status'])}>
                    <option value="active">Active</option>
                    <option value="on_leave">On Leave</option>
                  </Select>
                </Field>
              )}
              <Field label="Hire date"><Input className="w-full" type="date" value={draft.hire_date} disabled={busy} onChange={(ev) => set('hire_date')(ev.target.value)} /></Field>
              <Field label="Pay type">
                <Select className="w-full" value={draft.pay_type} disabled={busy} onChange={(ev) => set('pay_type')(ev.target.value)}>
                  <option value="">Not set</option>
                  <option value="hourly">Hourly</option>
                  <option value="salaried">Salaried</option>
                </Select>
              </Field>
              <label className="flex items-center gap-1.5 self-end pb-2 text-sm text-fg">
                <Checkbox checked={draft.is_manager} disabled={busy} onChange={(ev) => set('is_manager')(ev.target.checked)} />
                Manager
              </label>
              <Field label="Notes" wide><Textarea className="w-full" rows={2} value={draft.notes} disabled={busy} onChange={(ev) => set('notes')(ev.target.value)} /></Field>
              {e.source === 'adp' && (
                <p className="col-span-2 rounded-md bg-warning-soft px-3 py-2 text-xs text-warning-fg">
                  If a later ADP roster has a different value for name, payroll name, email, job title, ADP department or status
                  (or hire date and pay type, when the file has those columns), nothing is overwritten: the difference is listed
                  here and in Staff for Follow ADP or Keep Current.
                </p>
              )}
              <div className="col-span-2 flex items-center gap-2">
                <Button size="md" disabled={busy || !changes || !draft.display_name.trim() || !draft.payroll_name.trim() || (e.status === 'left' && !draft.left_on)}
                  onClick={() => run(async () => {
                    if (changes) await mdmApi.patch(`/staff/${e.id}`, changes)
                    setDraft(null); setSaved(true)
                  })}>{busy ? 'Saving…' : 'Save'}</Button>
                <Button variant="secondary" size="md" disabled={busy} onClick={() => setDraft(null)}>Cancel</Button>
                {changes && <span className="text-xs text-warning-fg">Not saved yet</span>}
              </div>
            </div>
          ) : (
            <>
              <dl className="grid grid-cols-2 gap-3">
                <Line label="Source">{SOURCE_LABEL[e.source]}</Line>
                <Line label="Payroll name">{e.payroll_name}</Line>
                <Line label="Work email">{e.work_email}</Line>
                <Line label="Job title">{e.job_title}</Line>
                <Line label="ADP department">{e.adp_department}</Line>
                <Line label="UniOps department">{e.department_name}</Line>
                {e.status === 'left' ? <Line label="Left on">{e.left_on && formatDate(e.left_on)}</Line>
                  : <Line label="Status">{STATUS_LABEL[e.status]}</Line>}
                <Line label="Hire date">{e.hire_date && formatDate(e.hire_date)}</Line>
                <Line label="Pay type">{e.pay_type && PAY_TYPE_LABEL[e.pay_type]}</Line>
                <Line label="Manager">{e.is_manager ? 'Yes' : 'No'}</Line>
                <div className="col-span-2"><Line label="Notes">{e.notes}</Line></div>
              </dl>
              {saved && <p className="mt-3 text-sm text-primary-700">Saved</p>}
            </>
          )}
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
