/**
 * Who gets told when an incident is reported.
 *
 * ★ HSE, round 4: "provide a way to manage the recipients list on incidents, not
 * on each form, rather create a master email distribution list where the email
 * templates are stored and editable by the HSE role."
 *
 * `ehs_config.incident_notify_groups` has been read by the notifier since the
 * first release and there has never been a screen to fill it in, so every
 * incident notice has gone to the role fallback — HSE and their coordinator —
 * whatever kind of incident it was. This is that screen. The list is one place
 * rather than a field on each form, which is the part HSE was asking for.
 *
 * ★ Keyed on the incident type, not the category. On the dev database 60
 * incidents carry 4 categories between them — only five of the twelve types
 * imply one — so a list configured per category would have applied to four
 * incidents and silently fallen through for the rest. Every incident has a type,
 * it is what the reporter picks on the form, and its code is an enum here rather
 * than a vocabulary entry, so it cannot be renamed out from under a list.
 * Pointed out in review by uniops-9a before the screen was built.
 *
 * The fallback is deliberate and stated on the screen: a category with nobody
 * named falls to "any incident", and if that is empty too it falls to HSE. An
 * unconfigured list must never mean an injury is reported to nobody.
 *
 * ★ Round 5, TC-15: "provide a way to manage the recipients list on incidents
 * when every status changes on an incident form. add this to the list of 'who
 * is told'." So the second half of this page: one list per change. Unlike the
 * lists above, an empty one sends nothing — these changes sent nothing before
 * there was a list, and HSE is usually the one making them.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Mail, X } from 'lucide-react'
import { api, epmsApi } from '@/lib/api'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import {
  Card, ErrorNote, LoadFailedNote, PageHeader, primaryButton, SectionTitle,
} from '@/components/ui'

/** The catch-all key the notifier already understands. */
const ANY = '*'

interface IncidentTypeOption {
  code: string
  label: string
  form_kind: string
}

interface Config {
  incident_notify_groups: Record<string, string[]>
  incident_status_notify: Record<string, string[]>
  incident_notify_include: Record<string, Include>
}

/** Round 6, TC-07: "a checkbox on all 'who is told' items to include the
 * requester and the requester's supervisor." Keyed like the lists: a type,
 * "*", or "status:<event>". */
interface Include { reporter: boolean; supervisor: boolean }
const NO_INCLUDE: Include = { reporter: false, supervisor: false }

/** The changes the server sends for — app.services.notify.STATUS_EVENTS. */
const STATUS_ROWS: { key: string; label: string; hint: string }[] = [
  { key: 'under_investigation', label: 'Investigation started',
    hint: 'When an incident is first classified and moves into investigation.' },
  { key: 'hse_reviewed', label: 'Reviewed by HSE',
    hint: 'Each time HSE records its review — the step before an injury or damage incident can close.' },
  { key: 'closed', label: 'Closed', hint: 'When the incident is closed.' },
  { key: 'reopened', label: 'Reopened', hint: 'When a closed incident is put back into investigation.' },
]

interface Directory {
  id: string
  full_name: string
  email: string
}

export default function NotifyGroupsPage() {
  const queryClient = useQueryClient()
  const [groups, setGroups] = useState<Record<string, string[]> | null>(null)
  const [onStatus, setOnStatus] = useState<Record<string, string[]> | null>(null)
  const [include, setInclude] = useState<Record<string, Include>>({})

  const config = useQuery({
    queryKey: ['settings', 'config'],
    queryFn: () => api.get<Config>('/api/v1/settings/config'),
  })
  const types = useQuery({
    queryKey: ['incident-types', 'all'],
    queryFn: () => api.get<IncidentTypeOption[]>('/api/v1/incidents/types'),
  })
  // Names for the ids already stored. Without this the screen shows uuids for
  // whoever was configured before, which is unreadable and untrustworthy.
  //
  // The directory lives in epms-api, which is where PersonPicker reads it from,
  // and it caps page_size at 100 — asking for 500 answers 422, which the first
  // run of this screen did. Paged until there are no more: these lists name a
  // handful of people each, but the directory they are drawn from is the whole
  // company and the hundredth name must not silently become a uuid.
  const people = useQuery({
    queryKey: ['directory', 'all'],
    queryFn: async () => {
      const all: Directory[] = []
      for (let page = 1; page <= 20; page += 1) {
        const batch = await epmsApi.get<{ items: Directory[] }>(
          `/api/v1/users/directory?page=${page}&page_size=100`)
        all.push(...(batch.items ?? []))
        if ((batch.items ?? []).length < 100) break
      }
      return { items: all }
    },
  })

  useEffect(() => {
    if (config.data && groups === null) {
      setGroups(config.data.incident_notify_groups ?? {})
      setOnStatus(config.data.incident_status_notify ?? {})
      setInclude(config.data.incident_notify_include ?? {})
    }
  }, [config.data, groups])

  const save = useMutation({
    mutationFn: () => api.put('/api/v1/settings/config', {
      incident_notify_groups: groups ?? {},
      incident_status_notify: onStatus ?? {},
      incident_notify_include: include,
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings', 'config'] }),
  })

  if (config.isError || types.isError) {
    return (
      <div className="p-4">
        <LoadFailedNote what="The notification settings" />
      </div>
    )
  }
  if (!groups || !onStatus) return <p className="p-4 text-sm text-neutral-500">Loading…</p>

  const nameOf = (id: string) =>
    (people.data?.items ?? []).find((p) => p.id === id)?.full_name ?? id

  const rows: { key: string; label: string; hint: string }[] = [
    {
      key: ANY,
      label: 'Any incident',
      hint: 'Told about every incident that has no list of its own.',
    },
    ...(types.data ?? []).map((t) => ({
      key: t.code,
      // The full option text is a sentence on the form; the first clause is the
      // name of the thing.
      label: t.label.split(/\s+[—–]\s+/)[0],
      hint: 'Told instead of the list above, when an incident is this kind.',
    })),
  ]

  const setFor = (key: string, ids: string[]) =>
    setGroups({ ...groups, [key]: ids })
  const setStatusFor = (key: string, ids: string[]) =>
    setOnStatus({ ...onStatus, [key]: ids })
  const includeFor = (key: string) => include[key] ?? NO_INCLUDE
  const setIncludeFor = (key: string, next: Include) =>
    setInclude({ ...include, [key]: next })

  return (
    <div className="p-4">
      <PageHeader
        title="Who is told about an incident"
        subtitle="One list for the plant, rather than a field on every form."
      />

      {people.isError && <LoadFailedNote what="The staff directory" />}

      <p className="mb-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
        <b>Their supervisor</b> is the supervisor recorded against the reporter under Portal →
        Admin → Users, or, where none is recorded, the person the reporter said they told on the
        form. <b>An anonymous report copies neither</b> — either would say who reported it.
      </p>

      <SectionTitle className="mb-1 mt-1">When an incident is reported</SectionTitle>
      {/* ★ Said here because it decides whether an empty list is safe. */}
      <p className="mb-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
        A kind of incident with nobody named falls through to <b>Any incident</b>,
        and if that is empty too it goes to HSE and the coordinator. An
        unconfigured list never means nobody is told.
      </p>

      {rows.map((row) => (
        <ListCard key={row.key} label={row.label} hint={row.hint}
                  ids={groups[row.key] ?? []} nameOf={nameOf}
                  include={includeFor(row.key)}
                  onInclude={(next) => setIncludeFor(row.key, next)}
                  empty={row.key === ANY
                    ? 'Nobody named — incidents go to HSE and the coordinator.'
                    : 'Nobody named — uses the list above.'}
                  onChange={(ids) => setFor(row.key, ids)} />
      ))}

      <SectionTitle className="mb-1 mt-6">When an incident’s status changes</SectionTitle>
      <p className="mb-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
        Each change has its own list. <b>Nobody named and nothing ticked means nobody is
        emailed</b> for that change — unlike a new report, which always reaches HSE. Emailed
        only when the status actually changes, so correcting a classification later does not
        send it again.
      </p>

      {STATUS_ROWS.map((row) => (
        <ListCard key={row.key} label={row.label} hint={row.hint}
                  ids={onStatus[row.key] ?? []} nameOf={nameOf}
                  include={includeFor(`status:${row.key}`)}
                  onInclude={(next) => setIncludeFor(`status:${row.key}`, next)}
                  empty="Nobody named — only the boxes ticked below are told."
                  onChange={(ids) => setStatusFor(row.key, ids)} />
      ))}

      <ErrorNote error={save.error} />
      <div className="mt-3 flex items-center justify-end gap-3">
        {save.isSuccess && (
          <span className="text-xs text-success-700">Saved.</span>
        )}
        <button type="button" className={primaryButton} disabled={save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}>
          {save.isPending ? 'Saving…' : 'Save the lists'}
        </button>
      </div>
    </div>
  )
}

/** One list: who is on it, a way to take somebody off, and a way to add. */
function ListCard({ label, hint, ids, nameOf, empty, onChange, include, onInclude }: {
  label: string; hint: string; ids: string[]; nameOf: (id: string) => string
  empty: string; onChange: (ids: string[]) => void
  include: Include; onInclude: (next: Include) => void
}) {
  const [adding, setAdding] = useState<PersonValue>(EMPTY_PERSON)
  return (
    <Card className="mb-2">
      <SectionTitle className="mb-0.5 flex items-center gap-1.5">
        <Mail className="h-3.5 w-3.5 text-neutral-400" aria-hidden />
        {label}
      </SectionTitle>
      <p className="mb-2 text-xs text-neutral-500">{hint}</p>

      {ids.length === 0 ? (
        <p className="mb-2 text-xs text-neutral-400">{empty}</p>
      ) : (
        <ul className="mb-2 flex flex-wrap gap-1.5">
          {ids.map((id) => (
            <li key={id}
                className="flex items-center gap-1 rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-800">
              {nameOf(id)}
              <button type="button" aria-label={`Remove ${nameOf(id)} from ${label}`}
                      onClick={() => onChange(ids.filter((x) => x !== id))}
                      className="text-neutral-400 hover:text-danger-600">
                <X className="h-3 w-3" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mb-2 flex flex-wrap gap-x-5 gap-y-1">
        <label className="flex min-h-[36px] items-center gap-2 text-sm text-neutral-800">
          <input type="checkbox" checked={include.reporter}
                 onChange={(e) => onInclude({ ...include, reporter: e.target.checked })} />
          The person who reported it
        </label>
        <label className="flex min-h-[36px] items-center gap-2 text-sm text-neutral-800">
          <input type="checkbox" checked={include.supervisor}
                 onChange={(e) => onInclude({ ...include, supervisor: e.target.checked })} />
          Their supervisor
        </label>
      </div>

      <PersonPicker
        value={adding}
        allowExternal={false}
        onChange={(v) => {
          if (v.userId && !ids.includes(v.userId)) {
            onChange([...ids, v.userId])
            setAdding(EMPTY_PERSON)
          } else {
            setAdding(v)
          }
        }}
      />
    </Card>
  )
}
