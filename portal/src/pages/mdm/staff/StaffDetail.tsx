import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { epmsApi, mdmApi } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import {
  INPUT, NcPill, PRIMARY_BTN, SECONDARY_BTN, SOURCE_LABEL, StaffShell, StatusPill, TD, TH, TODO_LABEL,
  targetText, useStaffPerm, type Department, type NcTodoRow, type StaffRow,
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
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd className="text-sm text-neutral-800">{children || '—'}</dd>
    </div>
  )
}

export default function StaffDetail() {
  const { id } = useParams()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { admin, can } = useStaffPerm()
  const [error, setError] = useState('')
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

  if (!e) return <StaffShell permission="employee.view"><p className="text-sm text-neutral-400">Loading…</p></StaffShell>

  const editable = can('employee.edit')
  const pending: { department_id?: string | null; notes?: string } = {}
  if (departmentId !== null && departmentId !== (e.department_id ?? '')) pending.department_id = departmentId || null
  if (notes !== null && notes !== (e.notes ?? '')) pending.notes = notes
  const changes = Object.keys(pending).length ? pending : null
  const applyDifference = (d: Difference) => {
    if (d.field === 'email' && !confirm(`This changes ${e.display_name}'s sign-in to ${d.employee}. Continue?`)) return
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
      <Link to="/mdm/staff" className="mb-3 inline-flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-800">
        <ArrowLeft className="h-4 w-4" />Staff
      </Link>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-neutral-900">{e.display_name}</h1>
        <span className="font-mono text-sm text-neutral-500">{e.employee_number}</span>
        <StatusPill status={e.status} />
        <div className="ml-auto flex gap-2">
          {admin && !e.user && e.status !== 'left' && (
            <button className={PRIMARY_BTN} onClick={() => navigate(`/mdm/users?from_employee=${e.id}`)}>Create user</button>
          )}
          {editable && e.status !== 'left' && <button className={SECONDARY_BTN} onClick={() => setLeaving(true)}>Mark as Left</button>}
          {editable && e.status === 'left' && (
            <button className={SECONDARY_BTN} disabled={busy} onClick={() => run(() => mdmApi.post(`/staff/${e.id}/reinstate`))}>Reinstate</button>
          )}
          {admin && <button className={SECONDARY_BTN} onClick={() => setCorrecting(true)}>Correct number</button>}
        </div>
      </div>

      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}

      {leaving && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-neutral-200 bg-white p-3">
          <label className="text-sm text-neutral-700">Left on <input type="date" className={`${INPUT} ml-1`} value={leftOn} onChange={(ev) => setLeftOn(ev.target.value)} /></label>
          {e.user?.is_active && (
            <label className="flex items-center gap-1.5 text-sm text-neutral-700">
              <input type="checkbox" checked={deactivate} onChange={(ev) => setDeactivate(ev.target.checked)} />
              Also deactivate their user ({e.user.email})
            </label>
          )}
          <button className={PRIMARY_BTN} disabled={!leftOn || busy} onClick={markLeft}>Confirm</button>
          <button className={SECONDARY_BTN} onClick={() => setLeaving(false)}>Cancel</button>
        </div>
      )}

      {correcting && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-neutral-200 bg-white p-3">
          <input className={INPUT} placeholder="New number (six digits)" value={number} onChange={(ev) => setNumber(ev.target.value)} />
          <input className={`${INPUT} min-w-64`} placeholder="Reason" value={reason} onChange={(ev) => setReason(ev.target.value)} />
          <button className={PRIMARY_BTN} disabled={!/^\d{6}$/.test(number) || !reason.trim() || busy}
            onClick={() => run(async () => {
              await mdmApi.post(`/staff/${e.id}/correct-number`, { new_number: number, reason })
              setCorrecting(false)
            })}>Save</button>
          <button className={SECONDARY_BTN} onClick={() => setCorrecting(false)}>Cancel</button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-neutral-200 bg-white p-4">
          <h2 className="mb-3 text-sm font-semibold text-neutral-900">Employee</h2>
          <dl className="grid grid-cols-2 gap-3">
            <Line label="Source">{SOURCE_LABEL[e.source]}</Line>
            <Line label="Payroll name">{e.payroll_name}</Line>
            <Line label="Work email">{e.work_email}</Line>
            <Line label="Job title">{e.job_title}</Line>
            <Line label="ADP department">{e.adp_department}</Line>
            <Line label="Left on">{e.left_on}</Line>
            <div className="col-span-2">
              <dt className="text-xs text-neutral-500">UniOps department</dt>
              {editable ? (
                <select className={`${INPUT} mt-1 w-full`} value={departmentId ?? e.department_id ?? ''} disabled={busy}
                  onChange={(ev) => { setDepartmentId(ev.target.value); setSaved(false) }}>
                  <option value="">Not set</option>
                  {depts?.items.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              ) : <dd className="text-sm text-neutral-800">{e.department_name ?? '—'}</dd>}
            </div>
            <div className="col-span-2">
              <dt className="text-xs text-neutral-500">Notes</dt>
              {editable ? (
                <textarea className={`${INPUT} mt-1 w-full`} rows={2} value={notes ?? e.notes ?? ''}
                  onChange={(ev) => { setNotes(ev.target.value); setSaved(false) }} />
              ) : <dd className="text-sm text-neutral-800">{e.notes ?? '—'}</dd>}
            </div>
          </dl>
          {editable && (changes || saved) && (
            <div className="mt-3 flex items-center gap-2">
              {changes ? (
                <>
                  <button className={PRIMARY_BTN} disabled={busy} onClick={() => run(async () => {
                    await mdmApi.patch(`/staff/${e.id}`, changes)
                    setDepartmentId(null); setNotes(null); setSaved(true)
                  })}>{busy ? 'Saving…' : 'Save'}</button>
                  <button className={SECONDARY_BTN} disabled={busy} onClick={() => { setDepartmentId(null); setNotes(null) }}>Cancel</button>
                  <span className="text-xs text-amber-700">Not saved yet</span>
                </>
              ) : <span className="text-sm text-[#085E5E]">Saved</span>}
            </div>
          )}
          {e.source === 'adp' && <p className="mt-3 text-xs text-neutral-400">Name, email, job title, ADP department and status come from ADP and are overwritten by each import.</p>}
        </section>

        <div className="space-y-4">
          <section className="rounded-xl border border-neutral-200 bg-white p-4">
            <h2 className="mb-3 text-sm font-semibold text-neutral-900">User</h2>
            {e.user ? (
              <>
                <p className="text-sm text-neutral-800">{e.user.full_name} · {e.user.email} {!e.user.is_active && '(inactive)'}</p>
                {e.differences.length > 0 && (
                  <table className="mt-3 w-full text-sm">
                    <thead><tr className="text-left text-xs text-neutral-500"><th className="py-1">Field</th><th>Employee</th><th>User</th><th /></tr></thead>
                    <tbody>
                      {e.differences.map((d) => (
                        <tr key={d.field} className="border-t border-neutral-100">
                          <td className="py-1.5">{d.label}</td>
                          <td>{d.employee ?? '—'}</td>
                          <td>{d.user ?? '—'}</td>
                          <td className="text-right">
                            {admin && <button className="text-xs font-medium text-[#085E5E] hover:underline" disabled={busy}
                              onClick={() => applyDifference(d)}>Apply to user</button>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            ) : <p className="text-sm text-neutral-500">No user account.</p>}
          </section>

          <section className="rounded-xl border border-neutral-200 bg-white p-4">
            <h2 className="mb-3 text-sm font-semibold text-neutral-900">NC</h2>
            <dl className="grid grid-cols-2 gap-3">
              <Line label="State"><NcPill state={e.nc_state} /></Line>
              <Line label="Person code">{e.nc.person_code}</Line>
              <Line label="NC department">{e.nc.department_code && `${e.nc.department_code} ${e.nc.department_name ?? ''}`}</Line>
              <Line label="Checked">{e.nc.checked_at && formatDate(e.nc.checked_at)}</Line>
            </dl>
            {e.todos.length > 0 && (
              <ul className="mt-3 space-y-1 text-sm">
                {e.todos.map((t) => <li key={t.id}><span className="font-medium">{TODO_LABEL[t.type]}</span> <span className="text-neutral-500">{targetText(t)}</span></li>)}
              </ul>
            )}
          </section>
        </div>
      </div>

      <section className="mt-4 overflow-hidden rounded-xl border border-neutral-200 bg-white">
        <h2 className="px-4 pt-4 text-sm font-semibold text-neutral-900">History</h2>
        <div className="mt-2 max-h-80 overflow-auto">
          <table className="w-full">
            <thead><tr><th className={TH}>When</th><th className={TH}>Field</th><th className={TH}>Old</th><th className={TH}>New</th><th className={TH}>By</th></tr></thead>
            <tbody>
              {e.history.map((h, i) => (
                <tr key={i} className="border-t border-neutral-100">
                  <td className={TD}>{formatDate(h.at)}</td>
                  <td className={TD}>{h.field}{h.reason ? ` (${h.reason})` : ''}</td>
                  <td className={TD}>{h.old_value ?? '—'}</td>
                  <td className={TD}>{h.new_value ?? '—'}</td>
                  <td className={TD}>{h.from_import ? 'ADP import' : h.by ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </StaffShell>
  )
}
