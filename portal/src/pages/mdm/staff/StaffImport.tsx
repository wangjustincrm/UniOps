import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Download, Upload } from 'lucide-react'
import { mdmApi, mdmDownload, mdmUpload } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { INPUT, PRIMARY_BTN, SECONDARY_BTN, StaffShell, TD, TH } from './shared'

interface Change { field: string; old: string | null; new: string | null }
interface Item {
  row?: number; employee_number?: string | null; display_name?: string; work_email?: string | null
  adp_department?: string | null; changes?: Change[]; kind?: string; employee_id?: string | null
  email_owner?: string | null; position_id?: string; payroll_name?: string; reasons?: string[]
}
interface Missing {
  employee_id: string; employee_number: string; display_name: string; adp_department: string | null
  resolution: string | null; reason: string | null
}
interface ImportView {
  id: string; status: string; roster_date: string; file_name: string; applied_at: string | null
  counts: Record<string, number>; unresolved_missing: number
  groups?: Record<string, Item[] | string[]>; missing?: Missing[]; warnings?: string[]; expired?: boolean
  todos?: number; nc_checked?: boolean
}

const CONFLICT: Record<string, string> = {
  non_adp: 'This number belongs to a Non-ADP employee',
  email_in_file: 'The same email is on two rows of the file',
  email_other_employee: 'The email belongs to another employee',
  email_other_user: 'The email belongs to a user with a different number',
}

function today(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function Group({ title, count, tone, children }: { title: string; count: number; tone?: string; children?: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-t border-neutral-100">
      <button type="button" className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm disabled:cursor-default"
        disabled={!count} onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown className="h-4 w-4 text-neutral-400" /> : <ChevronRight className="h-4 w-4 text-neutral-400" />}
        <span className="font-medium text-neutral-800">{title}</span>
        <span className={`rounded-full px-2 py-0.5 text-xs ${count ? tone ?? 'bg-neutral-100 text-neutral-600' : 'bg-neutral-100 text-neutral-400'}`}>{count}</span>
      </button>
      {open && <div className="max-h-72 overflow-auto px-4 pb-3 text-sm text-neutral-700">{children}</div>}
    </div>
  )
}

function Groups({ view, onConvert }: { view: ImportView; onConvert?: (id: string) => void }) {
  const g = (k: string) => (view.groups?.[k] ?? []) as Item[]
  const unmapped = (view.groups?.unmapped ?? []) as string[]
  const person = (i: Item) => `${i.employee_number} ${i.display_name ?? ''}`
  return (
    <div className="rounded-xl border border-neutral-200 bg-white">
      <Group title="New" count={g('new').length} tone="bg-green-50 text-green-700">
        <ul>{g('new').map((i) => <li key={i.employee_number!}>{person(i)} · {i.adp_department ?? 'no department'}</li>)}</ul>
      </Group>
      <Group title="Changed" count={g('changed').length} tone="bg-blue-50 text-blue-700">
        <ul className="space-y-1">
          {g('changed').map((i) => (
            <li key={i.employee_number!}>{person(i)}
              {i.changes?.map((c) => <div key={c.field} className="pl-4 text-xs text-neutral-500">{c.field}: {c.old ?? '—'} → {c.new ?? '—'}</div>)}
            </li>
          ))}
        </ul>
      </Group>
      <Group title="Unchanged" count={g('unchanged').length}>
        <ul>{g('unchanged').map((i) => <li key={i.employee_number!}>{person(i)}</li>)}</ul>
      </Group>
      <Group title="Conflicts (not applied)" count={g('conflicts').length} tone="bg-amber-50 text-amber-700">
        <ul className="space-y-1">
          {g('conflicts').map((i) => (
            <li key={i.row}>Row {i.row}: {person(i)} · {i.work_email} — {CONFLICT[i.kind ?? ''] ?? i.kind}{i.email_owner ? ` (${i.email_owner})` : ''}
              {i.kind === 'non_adp' && onConvert && i.employee_id && (
                <button className="ml-2 text-xs font-medium text-[#085E5E] hover:underline" onClick={() => onConvert(i.employee_id!)}>Convert to ADP</button>
              )}
            </li>
          ))}
        </ul>
      </Group>
      <Group title="Row errors (not applied)" count={g('errors').length} tone="bg-red-50 text-red-700">
        <ul>{g('errors').map((i) => <li key={i.row}>Row {i.row}: {i.position_id} {i.payroll_name} — {i.reasons?.join('; ')}</li>)}</ul>
      </Group>
      <Group title="Unmapped ADP departments" count={unmapped.length} tone="bg-amber-50 text-amber-700">
        <ul>{unmapped.map((d) => <li key={d}>{d || '(empty)'}</li>)}</ul>
        <Link className="mt-1 inline-block text-xs font-medium text-[#085E5E] hover:underline" to="/mdm/staff/department-mapping">Open Department Mapping</Link>
      </Group>
      <Group title="Missing from roster" count={g('missing').length} tone="bg-amber-50 text-amber-700">
        <ul>{g('missing').map((i) => <li key={i.employee_id!}>{person(i)}</li>)}</ul>
      </Group>
    </div>
  )
}

function MissingReview({ view, onChanged }: { view: ImportView; onChanged: () => void }) {
  const [drafts, setDrafts] = useState<Record<string, { left_on: string; reason: string }>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const rows = (view.missing ?? []).filter((m) => !m.resolution)
  if (!rows.length) return null
  const decide = async (id: string, body: object) => {
    if (busy) return
    setBusy(true); setError('')
    try {
      await mdmApi.post(`/staff/imports/${view.id}/missing/${id}`, body)
      onChanged()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const draft = (id: string) => drafts[id] ?? { left_on: '', reason: '' }
  const set = (id: string, patch: object) => setDrafts((d) => ({ ...d, [id]: { ...draft(id), ...patch } }))
  return (
    <section className="mt-4 rounded-xl border border-amber-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-neutral-900">Review employees missing from the roster ({rows.length})</h2>
      <p className="mb-3 text-xs text-neutral-500">People who left are not in the ADP file, so each one needs a decision. Nobody is marked Left automatically.</p>
      {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
      <div className="space-y-2">
        {rows.map((m) => (
          <div key={m.employee_id} className="flex flex-wrap items-center gap-2 text-sm">
            <Link className="w-56 text-[#085E5E] hover:underline" to={`/mdm/staff/${m.employee_id}`}>{m.employee_number} {m.display_name}</Link>
            <input type="date" className={INPUT} aria-label="Left on" value={draft(m.employee_id).left_on}
              onChange={(e) => set(m.employee_id, { left_on: e.target.value })} />
            <button className={SECONDARY_BTN} disabled={busy || !draft(m.employee_id).left_on}
              onClick={() => decide(m.employee_id, { action: 'left', left_on: draft(m.employee_id).left_on })}>Mark Left</button>
            <input className={`${INPUT} min-w-56`} placeholder="Reason to keep" value={draft(m.employee_id).reason}
              onChange={(e) => set(m.employee_id, { reason: e.target.value })} />
            <button className={SECONDARY_BTN} disabled={busy || !draft(m.employee_id).reason.trim()}
              onClick={() => decide(m.employee_id, { action: 'keep', reason: draft(m.employee_id).reason })}>Keep</button>
          </div>
        ))}
      </div>
    </section>
  )
}

export default function StaffImport() {
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [rosterDate, setRosterDate] = useState(today())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [resultId, setResultId] = useState<string | null>(null)
  const [opened, setOpened] = useState<string | null>(null)

  const { data: current } = useQuery<ImportView | null>({
    queryKey: ['staff-import-current'], queryFn: () => mdmApi.get('/staff/imports/current'),
  })
  const { data: history } = useQuery<{ items: ImportView[] }>({
    queryKey: ['staff-imports'], queryFn: () => mdmApi.get('/staff/imports'),
  })
  const shownId = resultId ?? opened
  const { data: shown } = useQuery<ImportView>({
    queryKey: ['staff-import', shownId], enabled: !!shownId,
    queryFn: () => mdmApi.get(`/staff/imports/${shownId}`),
  })

  const refresh = () => {
    for (const key of ['staff-import-current', 'staff-imports', 'staff-import', 'staff']) qc.invalidateQueries({ queryKey: [key] })
  }
  const run = async (action: () => Promise<void>) => {
    if (busy) return
    setBusy(true); setError('')
    try { await action(); refresh() } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const upload = () => run(async () => {
    const form = new FormData()
    form.append('file', file!)
    form.append('roster_date', rosterDate)
    await mdmUpload('/staff/imports', form)
    setFile(null); setResultId(null); setOpened(null)
  })

  return (
    <StaffShell permission="employee.import">
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}

      {current ? (
        <section>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-neutral-900">Preview: {current.file_name}, roster of {formatDate(current.roster_date)}</h2>
            <span className="text-xs text-neutral-500">Nothing is saved until you apply.</span>
            <div className="ml-auto flex gap-2">
              <button className={SECONDARY_BTN} disabled={busy} onClick={() => run(async () => { await mdmApi.post(`/staff/imports/${current.id}/discard`) })}>Discard</button>
              {current.expired
                ? <button className={PRIMARY_BTN} disabled={busy} onClick={() => run(async () => { await mdmApi.post(`/staff/imports/${current.id}/regenerate`) })}>Regenerate preview</button>
                : <button className={PRIMARY_BTN} disabled={busy} onClick={() => run(async () => {
                    const r = await mdmApi.post<ImportView>(`/staff/imports/${current.id}/apply`)
                    setResultId(r.id)
                  })}>{busy ? 'Working…' : 'Apply'}</button>}
            </div>
          </div>
          {current.expired && <p className="mb-2 text-sm text-amber-700">This preview is over 24 hours old. Regenerate it before applying.</p>}
          {current.warnings?.map((w) => <p key={w} className="mb-2 text-sm text-amber-700">{w}</p>)}
          <Groups view={current} onConvert={(id) => run(async () => {
            await mdmApi.post(`/staff/${id}/convert-to-adp`)
            await mdmApi.post(`/staff/imports/${current.id}/regenerate`)
          })} />
        </section>
      ) : (
        <section className="flex flex-wrap items-end gap-3 rounded-xl border border-neutral-200 bg-white p-4">
          <label className="text-sm text-neutral-700">ADP roster (.xlsx)
            <input type="file" accept=".xlsx" className={`${INPUT} mt-1 block`} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <label className="text-sm text-neutral-700">Roster date
            <input type="date" className={`${INPUT} mt-1 block`} value={rosterDate} onChange={(e) => setRosterDate(e.target.value)} />
          </label>
          <button className={PRIMARY_BTN} disabled={!file || !rosterDate || busy} onClick={upload}>
            <Upload className="h-4 w-4" />{busy ? 'Reading…' : 'Upload and preview'}
          </button>
        </section>
      )}

      {shown && shown.status === 'applied' && (
        <section className="mt-4">
          <div className="mb-2 flex items-center gap-2">
            <h2 className="text-sm font-semibold text-neutral-900">Result: roster of {formatDate(shown.roster_date)}</h2>
            <Link to="/mdm/staff/nc-todo" className="text-sm font-medium text-[#085E5E] hover:underline">{shown.todos ?? 0} NC to-do(s) created</Link>
          </div>
          <Groups view={shown} />
          <MissingReview view={shown} onChanged={refresh} />
        </section>
      )}

      <section className="mt-6 overflow-hidden rounded-xl border border-neutral-200 bg-white">
        <h2 className="px-4 pt-4 text-sm font-semibold text-neutral-900">Import history</h2>
        <div className="mt-2 max-h-80 overflow-auto">
          <table className="w-full">
            <thead><tr>
              <th className={TH}>Roster date</th><th className={TH}>Applied</th><th className={TH}>File</th>
              <th className={TH}>New</th><th className={TH}>Changed</th><th className={TH}>Conflicts</th><th className={TH}>Errors</th>
              <th className={TH}>To review</th><th className={TH} />
            </tr></thead>
            <tbody>
              {history?.items.map((h) => (
                <tr key={h.id} className="border-t border-neutral-100">
                  <td className={TD}>{formatDate(h.roster_date)}</td>
                  <td className={TD}>{formatDate(h.applied_at)}</td>
                  <td className={TD}>{h.file_name}</td>
                  <td className={TD}>{h.counts.new ?? 0}</td>
                  <td className={TD}>{h.counts.changed ?? 0}</td>
                  <td className={TD}>{h.counts.conflicts ?? 0}</td>
                  <td className={TD}>{h.counts.errors ?? 0}</td>
                  <td className={TD}>{h.unresolved_missing}</td>
                  <td className={`${TD} whitespace-nowrap text-right`}>
                    <button className="mr-3 text-xs font-medium text-[#085E5E] hover:underline" onClick={() => { setResultId(null); setOpened(h.id) }}>View</button>
                    <button className="inline-flex items-center gap-1 text-xs font-medium text-[#085E5E] hover:underline"
                      onClick={() => mdmDownload(`/staff/imports/${h.id}/file`, h.file_name)}><Download className="h-3 w-3" />File</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!history?.items.length && <p className="py-6 text-center text-sm text-neutral-400">No imports yet.</p>}
        </div>
      </section>
    </StaffShell>
  )
}
