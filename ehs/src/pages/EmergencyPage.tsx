/**
 * Emergency preparedness: the drills, and who can respond.
 *
 * A fire drill is a measurement, not a meeting that happened. Two numbers come
 * out of it — how long to get everyone out, and whether everyone was accounted
 * for — and this page puts both where they cannot be skimmed past. The
 * evacuation trend is first because a single time means nothing: 3:50 is good
 * or bad only against the last one.
 *
 * The responder list is computed from the training register rather than kept by
 * hand. A second list would drift within a month, and it would drift in the
 * direction of naming somebody whose ticket expired — which is the worst way
 * for this particular list to be wrong.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, HeartPulse, Plus, Siren, Timer, X } from 'lucide-react'
import { api } from '@/lib/api'
import { Pagination, SearchBox, useListView, DateRangeFilter } from '@/components/ListView'
import { formatDate } from '@/lib/formatDate'
import { Link, useSearchParams } from 'react-router-dom'
import { usePermissions } from '@/hooks/usePermissions'
import { SignaturePad } from '@/components/SignaturePad'
import { VocabularySelect, type VocabItem } from '@/components/VocabularyPicker'
import { DrillEvidence } from '@/components/DrillEvidence'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import {
  Card, Empty, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle, Tabs,
} from '@/components/ui'

interface Drill {
  id: string; drill_no: string; drill_type: string; scenario: string
  planned_for: string | null; held_on: string | null
  started_at: string | null; ended_at: string | null
  areas: { id: string; path: string }[]
  is_announced: boolean
  participants_count: number | null
  evacuation_seconds: number | null
  all_accounted_for: boolean | null
  missing_note: string | null
  observations: string | null; what_worked: string | null; what_failed: string | null
  root_cause_id: string | null; root_cause_label: string | null
  signature: string | null; signed_by_name: string | null; signed_at: string | null
  status: string; evaluated_at: string | null; evaluated_by_name: string | null
  created_by_name: string | null
}
interface TrendRow {
  drill_no: string; held_on: string | null; seconds: number
  announced: boolean; all_accounted_for: boolean | null
}
interface Responder {
  user_id: string; user_name: string; shift_code: string | null
  certified_on: string | null; expires_on: string | null
  is_current: boolean; designated_only: boolean
}
interface Coverage {
  total: number; current: number
  lapsed: { user_name: string; expires_on: string | null }[]
  by_shift: { shift: string; count: number }[]
  shift_unknown: number
}

const TYPES = [
  { key: 'fire', label: 'Fire' },
  { key: 'evacuation', label: 'Evacuation' },
  { key: 'spill', label: 'Chemical spill' },
  { key: 'rescue', label: 'Rescue' },
  { key: 'medical', label: 'Medical' },
  { key: 'shelter', label: 'Shelter in place' },
  { key: 'other', label: 'Other' },
]

const CAPACITIES = [
  { key: 'controller', label: 'Incident controller' },
  { key: 'warden', label: 'Warden' },
  { key: 'first_aider', label: 'First aider' },
  { key: 'observer', label: 'Observer' },
  { key: 'participant', label: 'Took part' },
]

const TABS = [
  { key: 'drills', label: 'Drills' },
  { key: 'responders', label: 'Who can respond' },
] as const
type TabKey = (typeof TABS)[number]['key']

function mmss(seconds: number): string {
  const m = Math.floor(seconds / 60)
  return `${m}:${String(seconds % 60).padStart(2, '0')}`
}

export default function EmergencyPage() {
  const { can } = usePermissions()
  const mayRun = can('ehs.firstaid.write')
  const [tab, setTab] = useState<TabKey>('drills')

  return (
    <div className="p-4">
      <PageHeader title="Emergency preparedness"
                  subtitle="Drills, what they measured, and who is qualified to respond" />
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === 'drills' && <DrillsTab mayRun={mayRun} />}
      {tab === 'responders' && <RespondersTab mayWriteTraining={can('ehs.training.write')} />}
    </div>
  )
}

// ── Drills ───────────────────────────────────────────────────────────────────

function DrillsTab({ mayRun }: { mayRun: boolean }) {
  const [creating, setCreating] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  // What the server will send at most: `list_drills` caps at 200 and no
  // parameter raises it. Far above this plant's volume, but the pager says so
  // rather than letting a date range read as "no drills that year".
  const DRILL_LIMIT = 200

  const drills = useQuery({
    queryKey: ['drills'],
    queryFn: () => api.get<Drill[]>('/api/v1/drills'),
  })
  const [search] = useSearchParams()
  // ★ `?q=` lets an action raised from a drill land on that drill's row: there
  // is no page for a single drill, and the register alone would not say which
  // one the link was about.
  const list = useListView(drills.data, (d) => [
    d.drill_no, d.scenario, d.drill_type, d.status,
    // The areas are a list; flattened so searching "packaging" finds a drill
    // that covered it.
    ...d.areas.map((a) => a.path),
  ], { dateOf: (d) => d.held_on ?? d.planned_for, query: search.get('q') ?? '' })
  const trend = useQuery({
    queryKey: ['drill-trend'],
    queryFn: () => api.get<TrendRow[]>('/api/v1/drills/trend?drill_type=fire'),
  })

  return (
    <>
      {(trend.data?.length ?? 0) > 1 && <EvacuationTrend rows={trend.data ?? []} />}

      {mayRun && (
        <div className="mb-3 flex justify-end">
          <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            Plan a drill
          </button>
        </div>
      )}
      {creating && <NewDrillForm onClose={() => setCreating(false)} />}

      <SearchBox value={list.query} onChange={list.setQuery}
                 placeholder="Search drills by number, scenario, type or area">
        <DateRangeFilter {...list.dateFilter} />
      </SearchBox>

      {drills.data?.length === 0 && !creating ? (
        <Empty>
          No drills recorded. A drill is worth recording for two numbers: how
          long it took to get everyone out, and whether everyone was accounted
          for.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {list.pageRows.map((d) => (
            <li key={d.id}>
              <Card className={'!p-0 ' + (d.all_accounted_for === false
                ? '!border-danger-400' : '')}>
                <button type="button" onClick={() => setOpen(open === d.id ? null : d.id)}
                        className="flex w-full items-start gap-3 p-3.5 text-left">
                  <Siren className="mt-0.5 h-5 w-5 shrink-0 text-neutral-400" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">
                      {TYPES.find((t) => t.key === d.drill_type)?.label ?? d.drill_type}
                      {' · '}
                      <span className="font-normal text-neutral-600">{d.scenario}</span>
                    </span>
                    <span className="mt-0.5 block text-xs text-neutral-500">
                      {d.drill_no}
                      {d.held_on ? ` · held ${formatDate(d.held_on)}`
                                 : d.planned_for ? ` · planned ${formatDate(d.planned_for)}` : ''}
                      {!d.is_announced && ' · unannounced'}
                      {d.participants_count !== null && ` · ${d.participants_count} people`}
                    </span>
                  </span>
                  <span className="flex shrink-0 flex-col items-end gap-1">
                    {d.evacuation_seconds !== null && (
                      <span className="font-mono text-lg font-bold tabular-nums text-neutral-900">
                        {mmss(d.evacuation_seconds)}
                      </span>
                    )}
                    <MusterPill drill={d} />
                  </span>
                </button>
                {open === d.id && <DrillDetail drill={d} mayRun={mayRun} />}
              </Card>
            </li>
          ))}
        </ul>
      )}

      <Pagination page={list.page} pageCount={list.pageCount} total={list.total}
                  onPage={list.setPage} noun="drills"
                  capped={(drills.data ?? []).length >= DRILL_LIMIT}
                  loaded={(drills.data ?? []).length} />
    </>
  )
}

function MusterPill({ drill }: { drill: Drill }) {
  if (drill.status === 'planned') {
    return (
      <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600">
        Planned
      </span>
    )
  }
  if (drill.all_accounted_for === null) {
    return (
      <span className="rounded-full bg-warning-50 px-2 py-0.5 text-[11px] font-semibold text-warning-700">
        Muster not answered
      </span>
    )
  }
  return drill.all_accounted_for ? (
    <span className="flex items-center gap-1 rounded-full bg-success-50 px-2 py-0.5 text-[11px] font-semibold text-success-700">
      <Check className="h-3 w-3" aria-hidden />
      All accounted for
    </span>
  ) : (
    <span className="flex items-center gap-1 rounded-full bg-danger-600 px-2 py-0.5 text-[11px] font-semibold text-white">
      <AlertTriangle className="h-3 w-3" aria-hidden />
      Somebody missing
    </span>
  )
}

/**
 * Evacuation times as bars, oldest on the left.
 *
 * Bars rather than a line, and drawn with divs rather than a chart library:
 * six numbers do not justify a dependency, and the shape — is it getting
 * shorter — reads the same either way.
 */
function EvacuationTrend({ rows }: { rows: TrendRow[] }) {
  const worst = Math.max(...rows.map((r) => r.seconds))
  const latest = rows[rows.length - 1]
  const previous = rows.length > 1 ? rows[rows.length - 2] : null
  const change = previous ? latest.seconds - previous.seconds : 0

  return (
    <Card className="mb-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <SectionTitle><Timer className="h-3.5 w-3.5" aria-hidden />
          Time to evacuate</SectionTitle>
        {previous && (
          <span className={`text-xs font-semibold ${
            change > 0 ? 'text-danger-700' : change < 0 ? 'text-success-700' : 'text-neutral-500'}`}>
            {change === 0 ? 'unchanged on last time'
              : `${change > 0 ? '+' : ''}${change}s on last time`}
          </span>
        )}
      </div>
      <ul className="flex flex-col gap-1.5">
        {rows.map((r) => (
          <li key={r.drill_no} className="flex items-center gap-2.5">
            <span className="w-20 shrink-0 text-right text-xs text-neutral-500">
              {r.held_on ? formatDate(r.held_on) : '—'}
            </span>
            <span className="w-12 shrink-0 text-right font-mono text-sm font-semibold tabular-nums text-neutral-900">
              {mmss(r.seconds)}
            </span>
            <span className={`h-4 shrink-0 rounded ${
              r.all_accounted_for === false ? 'bg-danger-500' : 'bg-primary-500'}`}
                  style={{ width: `${Math.max(4, (r.seconds / worst) * 60)}%` }} />
            <span className="min-w-0 truncate text-xs text-neutral-500">
              {r.announced ? 'announced' : 'unannounced'}
              {r.all_accounted_for === false && ' · somebody missing'}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-neutral-500">
        Announced drills measure the procedure; unannounced ones measure the
        people. They are not really comparable.
      </p>
    </Card>
  )
}

function DrillDetail({ drill, mayRun }: { drill: Drill; mayRun: boolean }) {
  const queryClient = useQueryClient()
  const evaluated = drill.status === 'evaluated'

  const participants = useQuery({
    queryKey: ['drill-participants', drill.id],
    queryFn: () => api.get<{ id: string; person_name: string; capacity: string }[]>(
      `/api/v1/drills/${drill.id}/participants`),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['drills'] })
    queryClient.invalidateQueries({ queryKey: ['drill-trend'] })
    queryClient.invalidateQueries({ queryKey: ['drill-participants', drill.id] })
  }

  return (
    <div className="border-t border-neutral-100 p-3.5">
      {drill.areas.length > 0 && (
        <p className="mb-2 text-xs text-neutral-500">
          Areas: {drill.areas.map((a) => a.path).join(' · ')}
        </p>
      )}

      {drill.missing_note && (
        <p className="mb-2 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {drill.missing_note}
        </p>
      )}
      {drill.what_worked && (
        <p className="mb-1.5 text-sm text-neutral-700">
          <span className="font-semibold">Worked:</span> {drill.what_worked}
        </p>
      )}
      {drill.what_failed && (
        <p className="mb-1.5 rounded-lg bg-warning-50 px-3 py-2 text-sm text-warning-700">
          <span className="font-semibold">Needs improvement:</span> {drill.what_failed}
        </p>
      )}

      {(participants.data?.length ?? 0) > 0 && (
        <ul className="mb-2 flex flex-wrap gap-1.5">
          {participants.data?.map((p) => (
            <li key={p.id}
                className="rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-600">
              <span className="font-semibold">
                {CAPACITIES.find((c) => c.key === p.capacity)?.label ?? p.capacity}
              </span>
              {' · '}{p.person_name}
            </li>
          ))}
        </ul>
      )}

      {evaluated ? (
        // ★ Evaluated used to collapse the whole drill to one sentence. The
        // root cause, the signature, the photographs and the action items all
        // stopped being visible the moment the drill was finished — which is
        // exactly when somebody wants to read them. Closed means not editable,
        // not invisible.
        <>
          {drill.root_cause_label && (
            <p className="mt-2 text-sm text-neutral-700">
              <span className="font-semibold">Root cause:</span> {drill.root_cause_label}
            </p>
          )}
          {drill.signed_by_name && (
            <p className="mt-1 text-xs text-neutral-500">
              Signed by {drill.signed_by_name}
              {drill.signed_at && ` on ${formatDate(drill.signed_at)}`}
            </p>
          )}
          <DrillEvidence drillId={drill.id} readOnly />
          <p className="mt-2 text-xs text-neutral-500">
            Evaluated {drill.evaluated_at && formatDate(drill.evaluated_at)} by{' '}
            {drill.evaluated_by_name}. The record is not edited after this.
          </p>
        </>
      ) : mayRun ? (
        <RecordPanel drill={drill} onSaved={invalidate} />
      ) : null}
    </div>
  )
}

function RecordPanel({ drill, onSaved }: { drill: Drill; onSaved: () => void }) {
  const [heldOn, setHeldOn] = useState(drill.held_on ?? '')
  // Asked in minutes and seconds, stored in seconds. A drill takes minutes,
  // and typing "255" for four and a quarter is how a mistyped digit becomes a
  // trend line nobody questions. The column stays in seconds because the
  // figures and the trend are drawn from it.
  const [minutes, setMinutes] = useState(
    drill.evacuation_seconds === null ? '' : String(Math.floor(drill.evacuation_seconds / 60)))
  const [seconds, setSeconds] = useState(
    drill.evacuation_seconds === null ? '' : String(drill.evacuation_seconds % 60))
  const [rootCause, setRootCause] = useState<VocabItem | null>(
    drill.root_cause_id
      ? { id: drill.root_cause_id, label: drill.root_cause_label ?? '' } as VocabItem
      : null)
  const [signature, setSignature] = useState<string | null>(drill.signature)
  const [count, setCount] = useState(
    drill.participants_count === null ? '' : String(drill.participants_count))
  const [muster, setMuster] = useState(
    drill.all_accounted_for === null ? '' : drill.all_accounted_for ? 'yes' : 'no')
  const [missing, setMissing] = useState(drill.missing_note ?? '')
  const [worked, setWorked] = useState(drill.what_worked ?? '')
  const [failed, setFailed] = useState(drill.what_failed ?? '')
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const [capacity, setCapacity] = useState('warden')

  const save = useMutation({
    mutationFn: () => api.patch(`/api/v1/drills/${drill.id}`, {
      held_on: heldOn || null,
      evacuation_seconds: minutes === '' && seconds === ''
        ? null
        : (Number(minutes || 0) * 60) + Number(seconds || 0),
      participants_count: count === '' ? null : Number(count),
      all_accounted_for: muster === '' ? null : muster === 'yes',
      missing_note: missing || null,
      what_worked: worked || null,
      what_failed: failed || null,
      root_cause_id: rootCause?.id ?? null,
      root_cause_label: rootCause?.label ?? null,
      // The name and time are stamped by the server, never sent from here.
      signature,
    }),
    onSuccess: onSaved,
  })
  const addPerson = useMutation({
    mutationFn: () => api.post(`/api/v1/drills/${drill.id}/participants`, {
      people: [{ user_id: person.userId, person_name: person.name, capacity }],
    }),
    onSuccess: () => { setPerson(EMPTY_PERSON); onSaved() },
  })
  const evaluate = useMutation({
    mutationFn: () => api.post(`/api/v1/drills/${drill.id}/evaluate`, {}),
    onSuccess: onSaved,
  })

  return (
    <div className="mt-2">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Held on">
          <input type="date" className={inputClass} value={heldOn}
                 onChange={(e) => setHeldOn(e.target.value)} />
        </Field>
        <Field label="Minutes to evacuate" hint="Or leave the clock times to derive it">
          <span className="flex items-center gap-1.5">
            <input className={inputClass} inputMode="numeric" value={minutes}
                   aria-label="Minutes to evacuate"
                   onChange={(e) => setMinutes(e.target.value.replace(/[^0-9]/g, ''))} />
            <span className="text-xs text-neutral-500">min</span>
            <input className={inputClass} inputMode="numeric" value={seconds}
                   aria-label="Seconds on top of the minutes"
                   onChange={(e) => setSeconds(e.target.value.replace(/[^0-9]/g, ''))} />
            <span className="text-xs text-neutral-500">sec</span>
          </span>
        </Field>
        <Field label="People out">
          <input className={inputClass} inputMode="numeric" value={count}
                 onChange={(e) => setCount(e.target.value.replace(/[^0-9]/g, ''))} />
        </Field>
      </div>

      <div className="mt-3">
        <Field label="Was everyone accounted for at the muster point?"
               hint="This is the drill's result. It cannot be evaluated until it is answered.">
          <select className={inputClass} value={muster}
                  onChange={(e) => setMuster(e.target.value)}>
            <option value="">Not answered</option>
            <option value="yes">Yes, everyone</option>
            <option value="no">No — somebody was missing</option>
          </select>
        </Field>
      </div>

      {muster === 'no' && (
        <div className="mt-2">
          <Field label="Who was missing, and why">
            <input className={inputClass} value={missing}
                   onChange={(e) => setMissing(e.target.value)} />
          </Field>
        </div>
      )}

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field label="What worked">
          <textarea className={`${inputClass} min-h-[60px] py-2`} value={worked}
                    onChange={(e) => setWorked(e.target.value)} />
        </Field>
        <Field label="What needs improvement"
               hint="Kept separate so it does not get softened into the box beside it">
          <textarea className={`${inputClass} min-h-[60px] py-2`} value={failed}
                    onChange={(e) => setFailed(e.target.value)} />
        </Field>
      </div>

      <div className="mt-3">
        <Field label="Root cause"
               hint="The same list the incident investigations draw on, so a cause that keeps coming back is countable across both">
          <VocabularySelect vocabulary="root_cause" value={rootCause?.id ?? null}
                            onChange={setRootCause} />
        </Field>
      </div>

      <DrillEvidence drillId={drill.id} />

      <div className="mt-3">
        <p className="mb-1 text-xs font-medium text-neutral-700">
          Signed by whoever ran the drill
        </p>
        <SignaturePad value={signature} onChange={setSignature} />
        {drill.signed_by_name && drill.signed_at && (
          <p className="mt-1 text-xs text-neutral-500">
            Signed by {drill.signed_by_name} on {formatDate(drill.signed_at)}.
          </p>
        )}
      </div>

      <div className="mt-3 rounded-lg border border-neutral-200 p-2.5">
        <p className="mb-1.5 text-xs font-medium text-neutral-700">
          Name the people whose role was itself being tested
        </p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <select className={`${inputClass} sm:w-52`} value={capacity}
                  onChange={(e) => setCapacity(e.target.value)}>
            {CAPACITIES.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
          </select>
          <div className="min-w-0 flex-1">
            <PersonPicker value={person} onChange={setPerson} />
          </div>
          <button type="button" className={secondaryButton}
                  disabled={!person.name.trim() || addPerson.isPending}
                  onClick={() => { if (!addPerson.isPending) addPerson.mutate() }}>
            Add
          </button>
        </div>
      </div>

      <ErrorNote error={save.error || evaluate.error || addPerson.error} />

      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button type="button" className={secondaryButton} disabled={save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}>
          Save
        </button>
        <button type="button" className={primaryButton}
                disabled={evaluate.isPending || save.isPending}
                onClick={async () => {
                  if (evaluate.isPending || save.isPending) return
                  await save.mutateAsync()
                  evaluate.mutate()
                }}>
          {evaluate.isPending ? 'Closing…' : 'Save and close the drill off'}
        </button>
      </div>
    </div>
  )
}

function NewDrillForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [type, setType] = useState('fire')
  const [scenario, setScenario] = useState('')
  const [plannedFor, setPlannedFor] = useState('')
  const [announced, setAnnounced] = useState(true)

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/drills', {
      drill_type: type, scenario,
      planned_for: plannedFor || null,
      is_announced: announced,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['drills'] })
      onClose()
    },
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">Plan a drill</h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Kind">
          <select className={inputClass} value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
          </select>
        </Field>
        <Field label="Planned for">
          <input type="date" className={inputClass} value={plannedFor}
                 onChange={(e) => setPlannedFor(e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Scenario">
            <textarea className={`${inputClass} min-h-[60px] py-2`} value={scenario}
                      onChange={(e) => setScenario(e.target.value)}
                      placeholder="Fire in the dryer bag house; full evacuation." />
          </Field>
        </div>
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm text-neutral-700">
        <input type="checkbox" checked={announced}
               onChange={(e) => setAnnounced(e.target.checked)} />
        Announced beforehand
      </label>
      <p className="mt-1 text-xs text-neutral-500">
        Announced drills measure the procedure. Unannounced ones measure the
        people, and give a slower, more honest time.
      </p>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!scenario.trim() || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Planning…' : 'Plan it'}
        </button>
      </div>
    </Card>
  )
}

// ── Responders ───────────────────────────────────────────────────────────────

function RespondersTab({ mayWriteTraining }: { mayWriteTraining: boolean }) {
  const responders = useQuery({
    queryKey: ['responders'],
    queryFn: () => api.get<Responder[]>('/api/v1/emergency/responders'),
  })
  const coverage = useQuery({
    queryKey: ['responder-coverage'],
    queryFn: () => api.get<Coverage>('/api/v1/emergency/coverage'),
  })

  const c = coverage.data

  return (
    <>
      <p className="mb-3 max-w-[68ch] text-sm text-neutral-600">
        Computed from the training register — whoever holds a current First Aid
        record. Nothing is maintained here, so it cannot drift from what the
        register says.
      </p>

      {mayWriteTraining && (
        <div className="mb-3 flex justify-end">
          {/* The list is derived, so there is nothing to edit here — the way
              to change who is a first aider is to record the course, and the
              panel used to say so without offering any way to get there. */}
          <Link to="/training?record=FIRSTAID" className={secondaryButton}>
            Record a First Aid completion
          </Link>
        </div>
      )}

      {c && (
        <>
          <div className="mb-4 flex flex-wrap gap-2">
            <Stat label="Current first aiders" value={String(c.current)}
                  tone={c.current === 0 ? 'warn' : 'ok'} />
            <Stat label="On the list at all" value={String(c.total)} />
          </div>

          {c.by_shift.length > 0 && (
            <ul className="mb-4 flex flex-wrap gap-2">
              {c.by_shift.map((s) => (
                <li key={s.shift}
                    className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2">
                  <p className="text-xs text-neutral-500">{s.shift}</p>
                  <p className="font-mono text-base font-bold tabular-nums text-neutral-900">
                    {s.count}
                  </p>
                </li>
              ))}
            </ul>
          )}

          {c.shift_unknown > 0 && (
            <p className="mb-4 rounded-lg bg-warning-50 px-3 py-2 text-sm text-warning-700">
              {c.shift_unknown} first aider{c.shift_unknown === 1 ? ' has' : 's have'} no
              shift recorded, so nobody can say whether every shift is covered.
              The shift comes from the worker&rsquo;s safety profile.
            </p>
          )}

          {c.lapsed.length > 0 && (
            <div className="mb-4 rounded-xl border-l-4 border-danger-500 bg-danger-50 p-3.5">
              <p className="text-sm font-semibold text-danger-700">
                {c.lapsed.length} lapsed or unverified
              </p>
              <ul className="mt-1 flex flex-wrap gap-1.5">
                {c.lapsed.map((l) => (
                  <li key={l.user_name}
                      className="rounded-full bg-white px-2.5 py-1 text-xs text-danger-700">
                    {l.user_name}
                    {l.expires_on ? ` · expired ${formatDate(l.expires_on)}`
                                  : ' · no record on file'}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {responders.data?.length === 0 ? (
        <Empty>
          Nobody is recorded as a first aider. Record a First Aid course
          completion against somebody and they appear here.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {responders.data?.map((r) => (
            <li key={r.user_id}>
              <Card className="!p-3">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <HeartPulse className={`h-4 w-4 shrink-0 ${
                    r.is_current ? 'text-success-600' : 'text-neutral-300'}`} aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-neutral-900">
                      {r.user_name}
                    </span>
                    <span className="block text-xs text-neutral-500">
                      {r.shift_code ?? 'Shift not recorded'}
                      {r.certified_on && ` · certified ${formatDate(r.certified_on)}`}
                    </span>
                  </span>
                  {r.designated_only ? (
                    <span className="rounded-full bg-warning-50 px-2 py-0.5 text-[11px] font-semibold text-warning-700">
                      Designated, no record
                    </span>
                  ) : r.is_current ? (
                    <span className="text-xs text-neutral-500">
                      until {r.expires_on ? formatDate(r.expires_on) : 'no expiry'}
                    </span>
                  ) : (
                    <span className="rounded-full bg-danger-50 px-2 py-0.5 text-[11px] font-semibold text-danger-700">
                      Expired{r.expires_on ? ` ${formatDate(r.expires_on)}` : ''}
                    </span>
                  )}
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'ok' | 'warn' }) {
  const colour = tone === 'warn' ? 'text-warning-700'
    : tone === 'ok' ? 'text-success-700' : 'text-neutral-900'
  return (
    <div className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2.5">
      <p className="text-xs text-neutral-500">{label}</p>
      <p className={`font-mono text-lg font-bold tabular-nums ${colour}`}>{value}</p>
    </div>
  )
}
