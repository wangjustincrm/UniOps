/**
 * Job safety analyses: the list, and one of them.
 *
 * The list is short and the detail is where the work happens, so both live
 * here rather than in two files with a route between them — the second page
 * would be a table and three buttons.
 *
 * The step editor is a table, not a method statement, because the row worth
 * finding is the one with a hazard and an empty control. Prose hides that; a
 * blank cell does not, and approving is refused while one exists.
 */
import EquipmentSelect, { EquipmentName } from '@/components/EquipmentSelect'
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { useTabTitle } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'
import { ClipboardList, Plus, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { Pagination, SearchBox, useListView, DateRangeFilter } from '@/components/ListView'
import { formatDate, todayForInput } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { useMyProfile } from '@/hooks/useMyProfile'
import { LocationSelect } from '@/components/LocationSelect'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { SignaturePad } from '@/components/SignaturePad'
import {
  Card, Empty, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle, LoadProblem, LoadFailedNote,
} from '@/components/ui'
import { anyFailed } from '@/lib/anyFailed'
import { CONTROL_LEVELS, type Hazard } from '@/pages/HazardsPage'
import { RatingBadge } from '@/components/RiskScoring'

interface Jsa {
  id: string; jsa_no: string; title: string; job_description: string | null
  version: number; location_id: string | null; location_path: string | null
  equipment_id?: string | null
  department_name: string | null; required_ppe: string | null
  status: string; approved_at: string | null; approved_by_name: string | null
  review_due_on: string | null; supersedes_id: string | null
  created_by_name: string | null; created_at: string
}
interface Step {
  id?: string; seq?: number; step: string; hazards: string | null; controls: string | null
  control_level: string | null; hazard_id: string | null; residual_rating: string | null
}
interface Signoff {
  id: string; version: number; user_id: string | null; person_name: string
  person_role: string | null; worked_on: string; signed_at: string; is_supervisor: boolean
}

const EMPTY_STEP: Step = {
  step: '', hazards: null, controls: null, control_level: null,
  hazard_id: null, residual_rating: null,
}

// ── List ─────────────────────────────────────────────────────────────────────

export default function JsaPage() {
  const navigate = useNavigate()
  const { can } = usePermissions()
  const mayWrite = can('ehs.action.write')
  const [creating, setCreating] = useState(false)

  // `list_jsa` caps at 200 with no way to ask for more; the pager says so if
  // the list ever comes back full, because a date range over a truncated list
  // finds nothing and looks like a year with no analyses.
  const JSA_LIMIT = 200

  const jsas = useQuery({
    queryKey: ['jsa'],
    queryFn: () => api.get<Jsa[]>('/api/v1/jsa?status=draft&status=active'),
  })
  const list = useListView(jsas.data, (j) => [
    j.jsa_no, j.title, j.job_description, j.location_path, j.department_name,
    j.required_ppe, j.status, j.created_by_name,
  ], { dateOf: (j) => j.created_at })

  return (
    <div className="p-4">
      <PageHeader title="Job safety analyses"
                  subtitle="A job, its steps, what can go wrong at each, and what stops it">
        {mayWrite && (
          <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            New analysis
          </button>
        )}
      </PageHeader>

      <SearchBox value={list.query} onChange={list.setQuery}
                 placeholder="Search by number, job, area, PPE or who wrote it">
        <DateRangeFilter {...list.dateFilter} />
      </SearchBox>

      {creating && <NewJsaForm onClose={() => setCreating(false)} />}

      {jsas.data?.length === 0 && !creating ? (
        <Empty>
          None written yet. An analysis is read and signed by the crew before
          they start a job — it is worth writing for the jobs where the method
          matters more than the person doing it.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {list.pageRows.map((j) => (
            <li key={j.id}>
              <button type="button" onClick={() => navigate(`/jsa/${j.id}`)}
                      className="flex w-full items-start gap-3 rounded-xl border border-neutral-200 bg-white p-3.5 text-left transition-colors hover:border-primary-300">
                <ClipboardList className="mt-0.5 h-5 w-5 shrink-0 text-primary-600" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-neutral-900">{j.title}</span>
                  <span className="mt-0.5 block text-xs text-neutral-500">
                    {j.jsa_no} · version {j.version}
                    {j.location_path && ` · ${j.location_path}`}<EquipmentName id={j.equipment_id} />
                  </span>
                </span>
                <StatusPill status={j.status} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <Pagination page={list.page} pageCount={list.pageCount} total={list.total}
                  onPage={list.setPage} noun="analyses"
                  capped={(jsas.data ?? []).length >= JSA_LIMIT}
                  loaded={(jsas.data ?? []).length} />
    </div>
  )
}

function StatusPill({ status }: { status: string }) {
  const tone = status === 'active' ? 'bg-success-50 text-success-700'
    : status === 'retired' ? 'bg-neutral-100 text-neutral-500'
    : 'bg-warning-50 text-warning-700'
  const label = status === 'active' ? 'In use'
    : status === 'retired' ? 'Superseded' : 'Draft'
  return (
    <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${tone}`}>
      {label}
    </span>
  )
}

function NewJsaForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [locationId, setLocationId] = useState<string | null>(null)
  const [equipmentId, setEquipmentId] = useState<string | null>(null)
  const [ppe, setPpe] = useState('')

  const create = useMutation({
    mutationFn: () => api.post<Jsa>('/api/v1/jsa', {
      title, job_description: description || null,
      location_id: locationId, equipment_id: equipmentId, required_ppe: ppe || null,
    }),
    onSuccess: (jsa) => {
      queryClient.invalidateQueries({ queryKey: ['jsa'] })
      onClose()
      navigate(`/jsa/${jsa.id}`)
    },
  })

  return (
    <Card className="mb-4">
      <h2 className="mb-3 text-sm font-semibold text-neutral-900">New analysis</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Field label="The job">
            <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)}
                   placeholder="Clearing a jam at the case sealer" />
          </Field>
        </div>
        <Field label="Where">
          <LocationSelect value={locationId} onChange={setLocationId} />
        </Field>
        {/* Round 10: the equipment register, as on the incident forms. */}
        <Field label="Equipment">
          <EquipmentSelect value={equipmentId} onChange={setEquipmentId} locationId={locationId} />
        </Field>
        <Field label="PPE for the whole job">
          <input className={inputClass} value={ppe} onChange={(e) => setPpe(e.target.value)}
                 placeholder="Cut-resistant gloves, safety glasses" />
        </Field>
        <div className="sm:col-span-2">
          <Field label="What the job involves">
            <textarea className={`${inputClass} min-h-[70px] py-2`} value={description}
                      onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
      </div>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!title.trim() || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Creating…' : 'Create and add steps'}
        </button>
      </div>
    </Card>
  )
}

// ── Detail ───────────────────────────────────────────────────────────────────

export function JsaDetailPage() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { can } = usePermissions()
  const mayWrite = can('ehs.action.write')
  const [signing, setSigning] = useState(false)

  const jsa = useQuery({
    queryKey: ['jsa', id],
    queryFn: () => api.get<Jsa>(`/api/v1/jsa/${id}`),
    enabled: Boolean(id),
  })
  const steps = useQuery({
    queryKey: ['jsa-steps', id],
    queryFn: () => api.get<Step[]>(`/api/v1/jsa/${id}/steps`),
    enabled: Boolean(id),
  })
  const signoffs = useQuery({
    queryKey: ['jsa-signoffs', id],
    queryFn: () => api.get<Signoff[]>(`/api/v1/jsa/${id}/signoffs`),
    enabled: Boolean(id),
  })

  useTabTitle(ehsRoutes, jsa.data?.jsa_no)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['jsa'] })
    queryClient.invalidateQueries({ queryKey: ['jsa-steps', id] })
    queryClient.invalidateQueries({ queryKey: ['jsa-signoffs', id] })
  }

  const failedToLoad = anyFailed(jsa, steps, signoffs)

  if (jsa.isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  // ★ The reason first. A refused or failed request leaves `data`
  // undefined exactly as a deleted record does, so this used to answer
  // "not found" to a 403 and to a server error alike.
  if (jsa.isError) return <LoadProblem error={jsa.error} noun="analysis" />
  if (!jsa.data) return <LoadProblem error={null} noun="analysis" />
  const j = jsa.data
  const isDraft = j.status === 'draft'

  // Grouped by the day the job was done — the same analysis is signed afresh
  // each time it runs, so one flat list would read as one enormous crew.
  const byDay = new Map<string, Signoff[]>()
  for (const s of signoffs.data ?? []) {
    const list = byDay.get(s.worked_on) ?? []
    list.push(s)
    byDay.set(s.worked_on, list)
  }

  return (
    <div className="p-4">
      <PageHeader title={j.title}
                  subtitle={`${j.jsa_no} · version ${j.version}`
                    + (j.location_path ? ` · ${j.location_path}` : '')}>
        <StatusPill status={j.status} />
      </PageHeader>

      <Card className="mb-4">
        {j.job_description && (
          <p className="mb-2 text-sm text-neutral-700">{j.job_description}</p>
        )}
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
          <div>
            <dt className="text-xs text-neutral-500">PPE for the job</dt>
            <dd className="text-sm text-neutral-900">{j.required_ppe ?? '—'}</dd>
          </div>
          <div>
            <dt className="text-xs text-neutral-500">Approved</dt>
            <dd className="text-sm text-neutral-900">
              {j.approved_at
                ? `${formatDate(j.approved_at)} by ${j.approved_by_name ?? 'unknown'}`
                : 'Not yet'}
            </dd>
          </div>
        </dl>
      </Card>

      {/* ★ An empty sign-off list reads as "nobody was briefed before this job",
          which is the finding an inspector writes up. It has to be told apart
          from "we could not load the sign-offs". */}
      {failedToLoad && <LoadFailedNote what="Part of this analysis" />}

      <StepEditor jsa={j} steps={steps.data ?? []} editable={mayWrite && isDraft}
                  onSaved={invalidate} />

      {mayWrite && (
        <JsaControls jsa={j} onChanged={invalidate}
                     onNewVersion={(draft) => navigate(`/jsa/${draft.id}`)} />
      )}

      {/* ── Who has signed ───────────────────────────────────────────────── */}
      <div className="mt-6 flex flex-wrap items-baseline justify-between gap-2">
        <SectionTitle>Signed before work</SectionTitle>
        {j.status === 'active' && (
          <button type="button" className={secondaryButton} onClick={() => setSigning(true)}>
            Sign for today&rsquo;s job
          </button>
        )}
      </div>

      {byDay.size === 0 ? (
        <Empty>
          {j.status === 'active'
            ? 'Nobody has signed this yet. The crew signs it on one device before starting.'
            : 'It can be signed once it has been approved.'}
        </Empty>
      ) : (
        <ul className="mt-2 flex flex-col gap-2">
          {[...byDay.entries()].map(([day, crew]) => (
            <li key={day}>
              <Card>
                <p className="text-xs font-semibold text-neutral-500">{formatDate(day)}</p>
                <ul className="mt-1 flex flex-wrap gap-1.5">
                  {crew.map((s) => (
                    <li key={s.id}
                        className="rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-600">
                      {s.person_name}
                      {s.is_supervisor && (
                        <span className="ml-1 font-semibold text-primary-700">supervisor</span>
                      )}
                      <span className="ml-1 text-neutral-400">v{s.version}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {signing && (
        <SignDialog jsa={j} onClose={() => setSigning(false)} onSigned={invalidate} />
      )}
    </div>
  )
}

function StepEditor(
  { jsa, steps, editable, onSaved }:
  { jsa: Jsa; steps: Step[]; editable: boolean; onSaved: () => void },
) {
  const [draft, setDraft] = useState<Step[]>(steps)
  const [dirty, setDirty] = useState(false)

  useEffect(() => { setDraft(steps); setDirty(false) }, [steps])

  const hazards = useQuery({
    queryKey: ['hazards', 'for-jsa'],
    queryFn: () => api.get<Hazard[]>('/api/v1/hazards?status=open&status=controlled'),
    enabled: editable,
  })

  const save = useMutation({
    mutationFn: () => api.put(`/api/v1/jsa/${jsa.id}/steps`, {
      steps: draft.filter((s) => s.step.trim()),
    }),
    onSuccess: () => { setDirty(false); onSaved() },
  })

  const set = (index: number, patch: Partial<Step>) => {
    setDraft((rows) => rows.map((r, i) => i === index ? { ...r, ...patch } : r))
    setDirty(true)
  }

  if (!editable) {
    return (
      <>
        <SectionTitle className="mb-2">Steps</SectionTitle>
        {steps.length === 0 ? (
          <Empty>No steps written.</Empty>
        ) : (
          <ol className="flex flex-col gap-2">
            {steps.map((s, i) => (
              <li key={s.id ?? i}>
                <Card>
                  <p className="text-sm font-medium text-neutral-900">
                    <span className="mr-2 font-mono text-xs text-neutral-400">{i + 1}</span>
                    {s.step}
                  </p>
                  {s.hazards && (
                    <p className="mt-1 text-xs text-danger-700">
                      <span className="font-semibold">Can go wrong:</span> {s.hazards}
                    </p>
                  )}
                  {s.controls && (
                    <p className="mt-0.5 text-xs text-neutral-700">
                      <span className="font-semibold">Controlled by:</span> {s.controls}
                      {s.control_level && ` (${
                        CONTROL_LEVELS.find((c) => c.key === s.control_level)?.label
                        ?? s.control_level})`}
                    </p>
                  )}
                  {s.residual_rating && (
                    <p className="mt-1"><RatingBadge rating={s.residual_rating} /></p>
                  )}
                </Card>
              </li>
            ))}
          </ol>
        )}
      </>
    )
  }

  return (
    <>
      <SectionTitle className="mb-2">Steps</SectionTitle>
      <ol className="flex flex-col gap-2">
        {draft.map((s, i) => (
          <li key={i}>
            <Card>
              <div className="flex items-start gap-2">
                <span className="mt-2.5 font-mono text-xs text-neutral-400">{i + 1}</span>
                <div className="min-w-0 flex-1 grid gap-2">
                  <Field label="What is done">
                    <input className={inputClass} value={s.step}
                           onChange={(e) => set(i, { step: e.target.value })}
                           placeholder="Stop the line and press the e-stop" />
                  </Field>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <Field label="What can go wrong">
                      <input className={inputClass} value={s.hazards ?? ''}
                             onChange={(e) => set(i, { hazards: e.target.value })} />
                    </Field>
                    <Field label="What stops it">
                      <input className={inputClass} value={s.controls ?? ''}
                             onChange={(e) => set(i, { controls: e.target.value })} />
                    </Field>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <Field label="Kind of control">
                      <select className={inputClass} value={s.control_level ?? ''}
                              onChange={(e) => set(i, { control_level: e.target.value || null })}>
                        <option value="">Not stated</option>
                        {CONTROL_LEVELS.map((c) => (
                          <option key={c.key} value={c.key}>{c.label}</option>
                        ))}
                      </select>
                    </Field>
                    <Field label="From the hazard register"
                           hint="Cite a standing hazard rather than restating it">
                      <select className={inputClass} value={s.hazard_id ?? ''}
                              onChange={(e) => set(i, { hazard_id: e.target.value || null })}>
                        <option value="">Not linked</option>
                        {hazards.data?.map((h) => (
                          <option key={h.id} value={h.id}>{h.hazard_no} — {h.title}</option>
                        ))}
                      </select>
                    </Field>
                  </div>
                  {(s.hazards ?? '').trim() && !(s.controls ?? '').trim() && (
                    <p className="rounded-lg bg-warning-50 px-2.5 py-1.5 text-xs text-warning-700">
                      This step names a hazard with nothing stopping it. It cannot be
                      approved like this.
                    </p>
                  )}
                </div>
                <button type="button" aria-label={`Remove step ${i + 1}`}
                        onClick={() => {
                          setDraft((rows) => rows.filter((_, k) => k !== i))
                          setDirty(true)
                        }}
                        className="mt-1.5 rounded p-1.5 text-neutral-400 hover:bg-danger-50 hover:text-danger-600">
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </Card>
          </li>
        ))}
      </ol>

      <div className="mt-2 flex flex-wrap justify-between gap-2">
        <button type="button" className={secondaryButton}
                onClick={() => { setDraft((r) => [...r, { ...EMPTY_STEP }]); setDirty(true) }}>
          <Plus className="h-4 w-4" aria-hidden />
          Add a step
        </button>
        <button type="button" className={primaryButton}
                disabled={!dirty || save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}>
          {save.isPending ? 'Saving…' : 'Save the steps'}
        </button>
      </div>
      <ErrorNote error={save.error} />
    </>
  )
}

function JsaControls(
  { jsa, onChanged, onNewVersion }:
  { jsa: Jsa; onChanged: () => void; onNewVersion: (draft: Jsa) => void },
) {
  const activate = useMutation({
    mutationFn: () => api.post(`/api/v1/jsa/${jsa.id}/activate`, {}),
    onSuccess: onChanged,
  })
  const revise = useMutation({
    mutationFn: () => api.post<Jsa>(`/api/v1/jsa/${jsa.id}/new-version`, {}),
    onSuccess: (draft) => { onChanged(); onNewVersion(draft) },
  })

  return (
    <div className="mt-3">
      <ErrorNote error={activate.error || revise.error} />
      <div className="flex flex-wrap justify-end gap-2">
        {jsa.status === 'draft' && (
          <button type="button" className={primaryButton} disabled={activate.isPending}
                  onClick={() => { if (!activate.isPending) activate.mutate() }}>
            {activate.isPending ? 'Approving…' : 'Approve for use'}
          </button>
        )}
        {jsa.status === 'active' && (
          <button type="button" className={secondaryButton} disabled={revise.isPending}
                  onClick={() => { if (!revise.isPending) revise.mutate() }}>
            {revise.isPending ? 'Creating…' : 'Revise it'}
          </button>
        )}
      </div>
      {jsa.status === 'active' && (
        <p className="mt-1 text-right text-xs text-neutral-500">
          Revising copies the steps into a new draft. Approving that one retires
          this version, and everybody signs the new one.
        </p>
      )}
    </div>
  )
}

/**
 * The crew signing before they start.
 *
 * One device passed round, like the toolbox talk — that is how it happens on
 * the floor, and a page each person has to log into would not get used.
 */
function SignDialog(
  { jsa, onClose, onSigned }: { jsa: Jsa; onClose: () => void; onSigned: () => void },
) {
  const profile = useMyProfile()
  const [workedOn, setWorkedOn] = useState(todayForInput)
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const [isSupervisor, setIsSupervisor] = useState(false)
  const [signature, setSignature] = useState<string | null>(null)
  const [crew, setCrew] = useState<
    { user_id: string | null; person_name: string; is_supervisor: boolean; signature: string | null }[]
  >([])

  const submit = useMutation({
    mutationFn: () => api.post(`/api/v1/jsa/${jsa.id}/sign`, {
      worked_on: workedOn, signers: crew,
    }),
    onSuccess: () => { onSigned(); onClose() },
  })

  const addPerson = () => {
    if (!person.name.trim() || !signature) return
    setCrew((c) => [...c, {
      user_id: person.userId, person_name: person.name,
      is_supervisor: isSupervisor, signature,
    }])
    setPerson(EMPTY_PERSON)
    setSignature(null)
    setIsSupervisor(false)
  }

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center overflow-y-auto bg-black/40 p-4 sm:items-center">
      <div role="dialog" aria-modal="true" aria-label="Sign before work"
           className="w-full max-w-lg rounded-xl bg-white p-4 shadow-xl">
        <h2 className="text-sm font-semibold text-neutral-900">
          Sign for {jsa.title}
        </h2>
        <p className="mb-3 text-xs text-neutral-500">
          Everyone doing this job signs on this device before starting.
        </p>

        <Field label="Day of the job">
          <input type="date" className={inputClass} value={workedOn}
                 onChange={(e) => setWorkedOn(e.target.value)} />
        </Field>

        {crew.length > 0 && (
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {crew.map((c, i) => (
              <li key={i}
                  className="flex items-center gap-1.5 rounded-full bg-primary-50 px-2.5 py-1 text-xs font-medium text-primary-700">
                {c.person_name}
                {c.is_supervisor && <span className="font-semibold">· supervisor</span>}
                <button type="button" aria-label={`Remove ${c.person_name}`}
                        onClick={() => setCrew((rows) => rows.filter((_, k) => k !== i))}>
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-3 rounded-lg border border-neutral-200 p-2.5">
          <Field label="Who is signing">
            <PersonPicker value={person} onChange={setPerson} />
          </Field>
          <label className="mt-2 flex items-center gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={isSupervisor}
                   onChange={(e) => setIsSupervisor(e.target.checked)} />
            Supervising this job
          </label>
          <div className="mt-2">
            <SignaturePad value={signature} onChange={setSignature}
                          savedSignature={profile.data?.signature_image ?? null} />
          </div>
          <div className="mt-2 flex justify-end">
            <button type="button" className={secondaryButton}
                    disabled={!person.name.trim() || !signature}
                    onClick={addPerson}>
              Add to the crew
            </button>
          </div>
        </div>

        <ErrorNote error={submit.error} />

        <div className="mt-3 flex justify-end gap-2">
          <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
          <button type="button" className={primaryButton}
                  disabled={crew.length === 0 || submit.isPending}
                  onClick={() => { if (!submit.isPending) submit.mutate() }}>
            {submit.isPending ? 'Recording…' : `Record ${crew.length} signature${crew.length === 1 ? '' : 's'}`}
          </button>
        </div>
      </div>
    </div>
  )
}
