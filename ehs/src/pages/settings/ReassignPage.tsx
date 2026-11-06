/**
 * Handing a leaver's open work to others.
 *
 * Round 10 (open box): "how does the hse role move items from a staff member
 * who has left the organization to a different staff member? this could be a
 * worker, team lead, supervisor or manager. hse role needs a way to review and
 * reassign items."
 *
 * Review first: what is still to be done with their name on it, kind by kind.
 * Then, for each kind, who takes it — or leave it where it is. The server
 * checks the whole plan and writes all of it or none of it, and says every
 * reason when it will not (services/reassign.py has the rules). What they
 * signed, reported or completed is the record and is not on this page.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import TabLink from '@/components/TabLink'
import { PersonPicker, type PersonValue } from '@/components/PersonPicker'
import {
  Card, Empty, inputClass, LoadProblem, PageHeader, primaryButton, SectionTitle,
} from '@/components/ui'

interface Person { id: string; full_name: string; email: string | null; department: string | null; is_active: boolean }
interface Item { id: string; ref: string | null; title: string; path: string | null }
interface Group { key: string; label: string; needs: string | null; items: Item[] }
interface Preview {
  leaver_id: string; leaver_name: string; is_active: boolean
  groups: Group[]; own_tasks: Record<string, number>; open_permits: Item[]
}

const OWN_TASK_NAMES: Record<string, string> = {
  ehs_read_sign: 'Documents to read and sign',
  ehs_course_attend: 'Training sessions to attend',
  ehs_cert_expiry: 'Their own certificates coming due',
}

export default function ReassignPage() {
  const [term, setTerm] = useState('')
  const [leaver, setLeaver] = useState<Person | null>(null)
  const people = useQuery({
    queryKey: ['staff-file', 'people', term.trim()],
    queryFn: () => api.get<Person[]>(
      `/api/v1/staff-file/people${term.trim() ? `?search=${encodeURIComponent(term.trim())}` : ''}`),
    enabled: !leaver,
  })
  return (
    <div className="mx-auto max-w-4xl p-4">
      <PageHeader title="Reallocate Items"
                  subtitle="Hand somebody's open work in Safety to others." />
      {leaver ? (
        <Plan person={leaver} onBack={() => setLeaver(null)} />
      ) : (
        <>
          <label className="relative mb-3 block">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" aria-hidden />
            <input className={`${inputClass} pl-9`} value={term} aria-label="Whose items"
                   placeholder="Whose items? Name or email" onChange={(e) => setTerm(e.target.value)} />
          </label>
          {people.isError && <LoadProblem error={people.error} noun="people" />}
          <ul className="flex flex-col gap-1.5">
            {(people.data ?? []).map((p) => (
              <li key={p.id}>
                <button type="button" onClick={() => setLeaver(p)}
                        className="flex w-full items-baseline gap-3 rounded-lg border border-neutral-200 bg-white px-3 py-2.5 text-left hover:border-primary-300">
                  <span className="text-sm font-medium text-neutral-900">{p.full_name}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-neutral-500">
                    {[p.department, p.email].filter(Boolean).join(' · ')}
                  </span>
                  {!p.is_active && (
                    <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600">Left</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function Plan({ person, onBack }: { person: Person; onBack: () => void }) {
  const queryClient = useQueryClient()
  const preview = useQuery({
    queryKey: ['reassign', person.id],
    queryFn: () => api.get<Preview>(`/api/v1/reassign/${person.id}`),
  })
  const [to, setTo] = useState<Record<string, PersonValue>>({})
  const [reason, setReason] = useState('')
  const go = useMutation({
    mutationFn: () => api.post<{ summary: { moved: Record<string, { label: string; to_name: string; count: number }>; own_tasks_closed: number } }>(
      `/api/v1/reassign/${person.id}`, {
        to: Object.fromEntries(Object.entries(to).map(([k, v]) => [k, v.userId])),
        reason: reason.trim(),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reassign', person.id] })
      setTo({})
    },
  })
  const problems = go.error instanceof ApiError
    ? ((go.error.body as { detail?: { problems?: string[] } })?.detail?.problems ?? null) : null

  if (preview.isPending) return <p className="text-sm text-neutral-500">Loading…</p>
  if (preview.isError || !preview.data) return <LoadProblem error={preview.error} noun="their open work" />
  const p = preview.data
  const own = Object.entries(p.own_tasks)
  const chosen = Object.values(to).filter((v) => v.userId).length
  const nothing = p.groups.length === 0 && own.length === 0

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-neutral-700">
        <span className="font-semibold">{p.leaver_name}</span>
        {p.is_active ? ' — still active in UniOps' : ' — has left'}
        {' · '}
        <button type="button" onClick={onBack} className="font-medium text-primary-700 hover:underline">
          Someone else
        </button>
      </p>

      {go.isSuccess && (
        <Card className="border-success-200 bg-success-50">
          <p className="text-sm font-semibold text-success-800">Handed over.</p>
          <ul className="mt-1 text-xs text-success-800">
            {Object.values(go.data.summary.moved).map((m) => (
              <li key={m.label}>{m.label}: {m.count} to {m.to_name}</li>
            ))}
            {go.data.summary.own_tasks_closed > 0 && (
              <li>Their own tasks closed: {go.data.summary.own_tasks_closed}</li>
            )}
          </ul>
        </Card>
      )}

      {nothing && !go.isSuccess && <Empty>Nothing in Safety is waiting on {p.leaver_name}.</Empty>}

      {p.groups.map((g) => (
        <Card key={g.key}>
          <div className="flex flex-wrap items-start gap-3">
            <div className="min-w-0 flex-1">
              <SectionTitle className="mb-1">{g.label} — {g.items.length}</SectionTitle>
              <ul className="flex flex-col gap-0.5 text-sm">
                {g.items.slice(0, 8).map((i) => (
                  <li key={i.id} className="truncate">
                    {i.path ? (
                      <TabLink to={i.path} className="text-primary-700 hover:underline">
                        {i.ref ? `${i.ref} ` : ''}{i.title}
                      </TabLink>
                    ) : <>{i.ref ? `${i.ref} ` : ''}{i.title}</>}
                  </li>
                ))}
                {g.items.length > 8 && <li className="text-xs text-neutral-500">… and {g.items.length - 8} more</li>}
              </ul>
            </div>
            <div className="w-full sm:w-72">
              <p className="mb-1 text-xs font-medium text-neutral-700">
                Hand to{g.needs ? <span className="font-normal text-neutral-500"> (needs {g.needs})</span> : null}
              </p>
              <PersonPicker value={to[g.key]} allowExternal={false}
                            placeholder="Leave where it is, or pick someone"
                            onChange={(v) => setTo({ ...to, [g.key]: v })} />
            </div>
          </div>
        </Card>
      ))}

      {own.length > 0 && (
        <Card>
          <SectionTitle className="mb-1">Closed, not moved (their own)</SectionTitle>
          <p className="mb-1 text-xs text-neutral-500">
            Only they could do these. They close with a note that they left — nothing is recorded as
            done by them.
          </p>
          <ul className="text-sm">
            {own.map(([type, n]) => <li key={type}>{OWN_TASK_NAMES[type] ?? type}: {n}</li>)}
          </ul>
        </Card>
      )}

      {p.open_permits.length > 0 && (
        <Card>
          <SectionTitle className="mb-1">Open permits — close or reissue</SectionTitle>
          <p className="mb-1 text-xs text-neutral-500">
            A permit stays in the name of whoever asked for it. Close each one, or raise a new one.
          </p>
          <ul className="text-sm">
            {p.open_permits.map((i) => (
              <li key={i.id}>
                <TabLink to={i.path!} className="text-primary-700 hover:underline">{i.ref} {i.title}</TabLink>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {!nothing && (
        <Card>
          <label className="block text-xs font-medium text-neutral-700">
            Why (kept with the handover)
            <textarea rows={2} className={`${inputClass} mt-1 py-2`} value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="e.g. Left the company on 24 October" />
          </label>
          {problems && (
            <div role="alert" className="mt-2 rounded-lg border border-warning-300 bg-warning-50 px-3 py-2 text-xs text-warning-800">
              <p className="font-semibold">Nothing was handed over:</p>
              <ul className="mt-1 list-disc pl-4">{problems.map((x) => <li key={x}>{x}</li>)}</ul>
            </div>
          )}
          {go.isError && !problems && (
            <p className="mt-2 text-xs text-danger-700">{(go.error as Error).message}</p>
          )}
          <div className="mt-2 flex items-center justify-end gap-3">
            <span className="text-xs text-neutral-500">
              {chosen} of {p.groups.length} kinds to hand over{own.length ? '; their own tasks close' : ''}
            </span>
            <button type="button" className={primaryButton}
                    disabled={reason.trim().length < 5 || (chosen === 0 && own.length === 0) || go.isPending}
                    onClick={() => { if (!go.isPending) go.mutate() }}>
              {go.isPending ? 'Handing over…' : 'Hand it over'}
            </button>
          </div>
        </Card>
      )}
    </div>
  )
}
