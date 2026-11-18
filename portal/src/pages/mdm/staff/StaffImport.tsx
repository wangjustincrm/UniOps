import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Upload } from 'lucide-react'
import { mdmApi, mdmDownload, mdmUpload } from '@/lib/api'
import { formatDate, todayISO } from '@/lib/utils'
import { StaffShell } from './shared'

import { Button, DisclosureButton, FileInput, Input, TBody, THead, Table, Td, Th, Tr, Badge, type BadgeTone } from '@uniops/shell'
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

/** Today at the plant (company time zone), YYYY-MM-DD. */
function today(): string {
  return todayISO()
}

function Group({ title, count, tone, children }: { title: string; count: number; tone?: BadgeTone; children?: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-t border-line-subtle">
      <DisclosureButton open={open} className="w-full px-4 py-2.5 text-sm [&>svg]:text-fg-muted disabled:cursor-default disabled:opacity-100"
        disabled={!count} onClick={() => setOpen((o) => !o)}>
        <span className="font-medium text-fg">{title}</span>
        {/* an empty group is the lighter grey → outline */}
        <Badge variant={count ? tone ?? 'neutral' : 'outline'}>{count}</Badge>
      </DisclosureButton>
      {open && <div className="max-h-72 overflow-auto px-4 pb-3 text-sm text-fg-secondary">{children}</div>}
    </div>
  )
}

function Groups({ view, onConvert }: { view: ImportView; onConvert?: (id: string) => void }) {
  const g = (k: string) => (view.groups?.[k] ?? []) as Item[]
  const unmapped = (view.groups?.unmapped ?? []) as string[]
  const person = (i: Item) => `${i.employee_number} ${i.display_name ?? ''}`
  return (
    <div className="rounded-xl border border-line bg-surface">
      <Group title="New" count={g('new').length} tone="success">
        <ul>{g('new').map((i) => <li key={i.employee_number!}>{person(i)} · {i.adp_department ?? 'no department'}</li>)}</ul>
      </Group>
      <Group title="Changed" count={g('changed').length} tone="blue">
        <ul className="space-y-1">
          {g('changed').map((i) => (
            <li key={i.employee_number!}>{person(i)}
              {i.changes?.map((c) => <div key={c.field} className="pl-4 text-xs text-fg-muted">{c.field}: {c.old ?? '—'} → {c.new ?? '—'}</div>)}
            </li>
          ))}
        </ul>
      </Group>
      <Group title="Unchanged" count={g('unchanged').length}>
        <ul>{g('unchanged').map((i) => <li key={i.employee_number!}>{person(i)}</li>)}</ul>
      </Group>
      <Group title="Conflicts (not applied)" count={g('conflicts').length} tone="warning">
        <ul className="space-y-1">
          {g('conflicts').map((i) => (
            <li key={i.row}>Row {i.row}: {person(i)} · {i.work_email} — {CONFLICT[i.kind ?? ''] ?? i.kind}{i.email_owner ? ` (${i.email_owner})` : ''}
              {i.kind === 'non_adp' && onConvert && i.employee_id && (
                <Button variant="link" className="ml-2 text-xs" onClick={() => onConvert(i.employee_id!)}>Convert to ADP</Button>
              )}
            </li>
          ))}
        </ul>
      </Group>
      <Group title="Row errors (not applied)" count={g('errors').length} tone="danger">
        <ul>{g('errors').map((i) => <li key={i.row}>Row {i.row}: {i.position_id} {i.payroll_name} — {i.reasons?.join('; ')}</li>)}</ul>
      </Group>
      <Group title="Unmapped ADP departments" count={unmapped.length} tone="warning">
        <ul>{unmapped.map((d) => <li key={d}>{d || '(empty)'}</li>)}</ul>
        <Link className="mt-1 inline-block text-xs font-medium text-primary-700 hover:underline" to="/mdm/staff/department-mapping">Open Department Mapping</Link>
      </Group>
      <Group title="Missing from roster" count={g('missing').length} tone="warning">
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
    <section className="mt-4 rounded-xl border border-warning-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-fg">Review employees missing from the roster ({rows.length})</h2>
      <p className="mb-3 text-xs text-fg-muted">People who left are not in the ADP file, so each one needs a decision. Nobody is marked Left automatically.</p>
      {error && <p className="mb-2 text-sm text-danger-fg">{error}</p>}
      <div className="space-y-2">
        {rows.map((m) => (
          <div key={m.employee_id} className="flex flex-wrap items-center gap-2 text-sm">
            <Link className="w-56 text-primary-700 hover:underline" to={`/mdm/staff/${m.employee_id}`}>{m.employee_number} {m.display_name}</Link>
            <Input type="date" aria-label="Left on" value={draft(m.employee_id).left_on}
              onChange={(e) => set(m.employee_id, { left_on: e.target.value })} />
            <Button variant="secondary" size="md" disabled={busy || !draft(m.employee_id).left_on}
              onClick={() => decide(m.employee_id, { action: 'left', left_on: draft(m.employee_id).left_on })}>Mark Left</Button>
            <Input className="min-w-56" placeholder="Reason to keep" value={draft(m.employee_id).reason}
              onChange={(e) => set(m.employee_id, { reason: e.target.value })} />
            <Button variant="secondary" size="md" disabled={busy || !draft(m.employee_id).reason.trim()}
              onClick={() => decide(m.employee_id, { action: 'keep', reason: draft(m.employee_id).reason })}>Keep</Button>
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
      {error && <p className="mb-3 text-sm text-danger-fg">{error}</p>}

      {current ? (
        <section>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-fg">Preview: {current.file_name}, roster of {formatDate(current.roster_date)}</h2>
            <span className="text-xs text-fg-muted">Nothing is saved until you apply.</span>
            <div className="ml-auto flex gap-2">
              <Button variant="secondary" size="md" disabled={busy} onClick={() => run(async () => { await mdmApi.post(`/staff/imports/${current.id}/discard`) })}>Discard</Button>
              {current.expired
                ? <Button size="md" disabled={busy} onClick={() => run(async () => { await mdmApi.post(`/staff/imports/${current.id}/regenerate`) })}>Regenerate preview</Button>
                : <Button size="md" disabled={busy} onClick={() => run(async () => {
                    const r = await mdmApi.post<ImportView>(`/staff/imports/${current.id}/apply`)
                    setResultId(r.id)
                  })}>{busy ? 'Working…' : 'Apply'}</Button>}
            </div>
          </div>
          {current.expired && <p className="mb-2 text-sm text-warning-fg">This preview is over 24 hours old. Regenerate it before applying.</p>}
          {current.warnings?.map((w) => <p key={w} className="mb-2 text-sm text-warning-fg">{w}</p>)}
          <Groups view={current} onConvert={(id) => run(async () => {
            await mdmApi.post(`/staff/${id}/convert-to-adp`)
            await mdmApi.post(`/staff/imports/${current.id}/regenerate`)
          })} />
        </section>
      ) : (
        <section className="flex flex-wrap items-end gap-3 rounded-xl border border-line bg-surface p-4">
          <label className="text-sm text-fg-secondary">ADP roster (.xlsx)
            <FileInput accept=".xlsx" className="mt-1 block" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <label className="text-sm text-fg-secondary">Roster date
            <Input type="date" className="mt-1 block" value={rosterDate} onChange={(e) => setRosterDate(e.target.value)} />
          </label>
          <Button size="md" disabled={!file || !rosterDate || busy} onClick={upload}>
            <Upload className="h-4 w-4" />{busy ? 'Reading…' : 'Upload and preview'}
          </Button>
        </section>
      )}

      {shown && shown.status === 'applied' && (
        <section className="mt-4">
          <div className="mb-2 flex items-center gap-2">
            <h2 className="text-sm font-semibold text-fg">Result: roster of {formatDate(shown.roster_date)}</h2>
            <Link to="/mdm/staff/nc-todo" className="text-sm font-medium text-primary-700 hover:underline">{shown.todos ?? 0} NC to-do(s) created</Link>
          </div>
          <Groups view={shown} />
          <MissingReview view={shown} onChanged={refresh} />
        </section>
      )}

      <section className="mt-6 overflow-hidden rounded-xl border border-line bg-surface">
        <h2 className="px-4 pt-4 text-sm font-semibold text-fg">Import history</h2>
        <div className="mt-2 max-h-80 overflow-auto">
          <Table>
            <THead><Tr>
              <Th>Roster date</Th><Th>Applied</Th><Th>File</Th>
              <Th>New</Th><Th>Changed</Th><Th>Conflicts</Th><Th>Errors</Th>
              <Th>To review</Th><Th />
            </Tr></THead>
            <TBody>
              {history?.items.map((h) => (
                <Tr key={h.id}>
                  <Td>{formatDate(h.roster_date)}</Td>
                  <Td>{formatDate(h.applied_at)}</Td>
                  <Td>{h.file_name}</Td>
                  <Td>{h.counts.new ?? 0}</Td>
                  <Td>{h.counts.changed ?? 0}</Td>
                  <Td>{h.counts.conflicts ?? 0}</Td>
                  <Td>{h.counts.errors ?? 0}</Td>
                  <Td>{h.unresolved_missing}</Td>
                  <Td className="whitespace-nowrap text-right">
                    <Button variant="link" className="mr-3 text-xs" onClick={() => { setResultId(null); setOpened(h.id) }}>View</Button>
                    <Button variant="link" className="text-xs"
                      onClick={() => mdmDownload(`/staff/imports/${h.id}/file`, h.file_name)}><Download className="h-3 w-3" />File</Button>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
          {!history?.items.length && <p className="py-6 text-center text-sm text-fg-muted">No imports yet.</p>}
        </div>
      </section>
    </StaffShell>
  )
}
