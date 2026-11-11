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
import EquipmentSelect, { EquipmentName, useEquipmentName } from '@/components/EquipmentSelect'
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
import { VocabularyMultiSelect, VocabularySelect } from '@/components/VocabularyPicker'
import { PhotoCapture } from '@/components/PhotoCapture'
import { RecordPhotos, uploadPhotos } from '@/components/RecordPhotos'
import type { CompressedImage } from '@/lib/imageCompress'

type Picked = { id: string; label: string }

interface Jsa {
  id: string; jsa_no: string; title: string; job_description: string | null
  version: number; location_id: string | null; location_path: string | null
  equipment_id?: string | null
  department_name: string | null; required_ppe: string | null
  /** Round 12, JSA-03 and JSA-01. */
  ppe_items?: Picked[]; system_id?: string | null; system_label?: string | null
  status: string; approved_at: string | null; approved_by_name: string | null
  review_due_on: string | null; supersedes_id: string | null
  created_by_name: string | null; created_at: string
}
interface Step {
  id?: string; seq?: number; step: string; hazards: string | null; controls: string | null
  control_level: string | null; hazard_id: string | null; residual_rating: string | null
  /** Round 12, JSA-05 / Q12: picked per step. Saved; sent back as ids. */
  hazard_items?: Picked[]; control_items?: Picked[]
  hazard_ids?: string[]; control_ids?: string[]
}

/** What the step editor holds: the saved picks as ids. */
function editable(s: Step): Step {
  return {
    ...s,
    hazard_ids: s.hazard_ids ?? (s.hazard_items ?? []).map((x) => x.id),
    control_ids: s.control_ids ?? (s.control_items ?? []).map((x) => x.id),
  }
}

function stepBody(s: Step) {
  return {
    step: s.step.trim(), hazards: s.hazards || null, controls: s.controls || null,
    control_level: s.control_level, hazard_id: s.hazard_id, residual_rating: s.residual_rating,
    hazard_ids: s.hazard_ids ?? [], control_ids: s.control_ids ?? [],
  }
}

/** ★ The approval gate, as the server applies it (crud/hazard.py activate). */
function uncontrolled(s: Step): boolean {
  const hazard = (s.hazards ?? '').trim() || (s.hazard_ids ?? []).length || s.hazard_id
  const control = (s.controls ?? '').trim() || (s.control_ids ?? []).length
  return Boolean(hazard) && !control
}
interface Signoff {
  id: string; version: number; user_id: string | null; person_name: string
  person_role: string | null; worked_on: string; signed_at: string; is_supervisor: boolean
}

const EMPTY_STEP: Step = {
  step: '', hazards: null, controls: null, control_level: null,
  hazard_id: null, residual_rating: null, hazard_ids: [], control_ids: [],
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
    j.required_ppe, ...(j.ppe_items ?? []).map((p) => p.label), j.system_label,
    j.status, j.created_by_name,
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
                    {j.system_label && ` · ${j.system_label}`}
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
  const [systemId, setSystemId] = useState<string | null>(null)
  const [ppeIds, setPpeIds] = useState<string[]>([])
  // ★ Round 12, JSA-04: the steps on the same page, saved with it.
  const [steps, setSteps] = useState<Step[]>([{ ...EMPTY_STEP }])
  const [photos, setPhotos] = useState<CompressedImage[]>([])
  // Remembered once made: a failed photo upload must not make a second JSA.
  const [made, setMade] = useState<Jsa | null>(null)

  const create = useMutation({
    mutationFn: async () => {
      const jsa = made ?? await api.post<Jsa>('/api/v1/jsa', {
        title, job_description: description || null,
        location_id: locationId, equipment_id: equipmentId,
        system_id: systemId, ppe_ids: ppeIds,
        steps: steps.filter((st) => st.step.trim()).map(stepBody),
      })
      setMade(jsa)
      await uploadPhotos('jsa', jsa.id, photos)
      return jsa
    },
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
        {/* ★ Round 12, JSA-01/02: the System is saved and narrows the equipment. */}
        <Field label="System">
          <VocabularySelect vocabulary="equipment_system" value={systemId}
                            onChange={(item) => { setSystemId(item?.id ?? null); setEquipmentId(null) }}
                            placeholder="Any System" />
        </Field>
        {/* Round 10: the equipment register, as on the incident forms. */}
        <Field label="Equipment">
          <EquipmentSelect value={equipmentId} onChange={setEquipmentId} locationId={locationId}
                           systemId={systemId} />
        </Field>
        {/* ★ Round 12, JSA-03: picked from HSE's PPE list. */}
        <Field label="PPE for the whole job">
          <VocabularyMultiSelect vocabulary="ppe" value={ppeIds} onChange={(ids) => setPpeIds(ids)}
                                 placeholder="Search the PPE list…" />
        </Field>
        <div className="sm:col-span-2">
          <Field label="What the job involves">
            <textarea className={`${inputClass} min-h-[70px] py-2`} value={description}
                      onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
      </div>
      <div className="mt-4 border-t border-neutral-100 pt-3">
        <SectionTitle className="mb-2">Steps</SectionTitle>
        <StepList steps={steps} onChange={setSteps} />
      </div>

      <div className="mt-4 border-t border-neutral-100 pt-3">
        <SectionTitle className="mb-1.5">Photographs</SectionTitle>
        <PhotoCapture photos={photos} onChange={setPhotos} />
      </div>

      <ErrorNote error={create.error} />
      {made && create.isError && (
        <p className="mt-2 text-xs text-neutral-600">
          {made.jsa_no} was saved. Pressing Create again sends the photographs to it rather than
          making a second one.
        </p>
      )}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!title.trim() || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
    </Card>
  )
}

/** ★ B3/B4 review M5: a draft's details, System and PPE could not be changed
 *  once it was made — nothing called the PATCH — so an analysis made before
 *  round 12 could never be given a System or PPE from the list. */
function JsaDetailsForm({ jsa, onClose }: { jsa: Jsa; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState(jsa.title)
  const [description, setDescription] = useState(jsa.job_description ?? '')
  const [locationId, setLocationId] = useState<string | null>(jsa.location_id)
  const [equipmentId, setEquipmentId] = useState<string | null>(jsa.equipment_id ?? null)
  const [systemId, setSystemId] = useState<string | null>(jsa.system_id ?? null)
  const [ppeIds, setPpeIds] = useState<string[]>((jsa.ppe_items ?? []).map((p) => p.id))
  const save = useMutation({
    mutationFn: () => api.patch<Jsa>(`/api/v1/jsa/${jsa.id}`, {
      title, job_description: description || null, location_id: locationId,
      equipment_id: equipmentId, system_id: systemId, ppe_ids: ppeIds,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jsa'] })
      queryClient.invalidateQueries({ queryKey: ['change-log'] })
      onClose()
    },
  })
  return (
    <Card className="mb-4">
      <h2 className="mb-3 text-sm font-semibold text-neutral-900">Details of {jsa.jsa_no}</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Field label="The job">
            <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
        </div>
        <Field label="Where">
          <LocationSelect value={locationId} onChange={setLocationId} />
        </Field>
        <Field label="System">
          <VocabularySelect vocabulary="equipment_system" value={systemId}
                            onChange={(item) => {
                              const next = item?.id ?? null
                              if (next !== systemId) setEquipmentId(null)
                              setSystemId(next)
                            }}
                            placeholder="Any System" />
        </Field>
        <Field label="Equipment">
          <EquipmentSelect value={equipmentId} onChange={setEquipmentId} locationId={locationId}
                           systemId={systemId} />
        </Field>
        <Field label="PPE for the whole job">
          <VocabularyMultiSelect vocabulary="ppe" value={ppeIds} onChange={(ids) => setPpeIds(ids)}
                                 placeholder="Search the PPE list…" />
          {jsa.required_ppe && (
            <p className="mt-1 text-xs text-neutral-500">Typed before: {jsa.required_ppe}</p>
          )}
        </Field>
        <div className="sm:col-span-2">
          <Field label="What the job involves">
            <textarea className={`${inputClass} min-h-[70px] py-2`} value={description}
                      onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
      </div>
      <ErrorNote error={save.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!title.trim() || save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}>
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Card>
  )
}

/** The step rows, used on the new form and on a draft. */
function StepList({ steps, onChange }: { steps: Step[]; onChange: (next: Step[]) => void }) {
  const hazards = useQuery({
    queryKey: ['hazards', 'for-jsa'],
    queryFn: () => api.get<Hazard[]>('/api/v1/hazards?status=open&status=controlled'),
  })
  const set = (index: number, patch: Partial<Step>) =>
    onChange(steps.map((r, i) => i === index ? { ...r, ...patch } : r))
  return (
    <>
      <ol className="flex flex-col gap-2">
        {steps.map((s, i) => (
          <li key={i}>
            <Card>
              <div className="flex items-start gap-2">
                <span className="mt-2.5 font-mono text-xs text-neutral-400">{i + 1}</span>
                <div className="grid min-w-0 flex-1 gap-2">
                  <Field label="What is done">
                    <input className={inputClass} value={s.step}
                           onChange={(e) => set(i, { step: e.target.value })}
                           placeholder="Stop the line and press the e-stop" />
                  </Field>
                  {/* ★ Round 12, JSA-05 / Q12: hazards and controls per step,
                      from HSE's lists; the words stay for what the lists lack. */}
                  <div className="grid gap-2 sm:grid-cols-2">
                    <Field label="What can go wrong">
                      <VocabularyMultiSelect vocabulary="hazard" value={s.hazard_ids ?? []}
                                             onChange={(ids) => set(i, { hazard_ids: ids })}
                                             placeholder="Search the hazard list…" />
                      <input className={`${inputClass} mt-1`} value={s.hazards ?? ''}
                             aria-label={`Step ${i + 1}: what can go wrong, in words`}
                             placeholder="Anything else, in words"
                             onChange={(e) => set(i, { hazards: e.target.value })} />
                    </Field>
                    <Field label="What stops it">
                      <VocabularyMultiSelect vocabulary="hazard_control" value={s.control_ids ?? []}
                                             onChange={(ids) => set(i, { control_ids: ids })}
                                             placeholder="Search the hazard controls…" />
                      <input className={`${inputClass} mt-1`} value={s.controls ?? ''}
                             aria-label={`Step ${i + 1}: what stops it, in words`}
                             placeholder="Anything else, in words"
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
                  {uncontrolled(s) && (
                    <p className="rounded-lg bg-warning-50 px-2.5 py-1.5 text-xs text-warning-700">
                      This step names a hazard with nothing stopping it. It cannot be
                      approved like this.
                    </p>
                  )}
                </div>
                <button type="button" aria-label={`Remove step ${i + 1}`}
                        onClick={() => onChange(steps.filter((_, k) => k !== i))}
                        className="mt-1.5 rounded p-1.5 text-neutral-400 hover:bg-danger-50 hover:text-danger-600">
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </Card>
          </li>
        ))}
      </ol>
      <button type="button" className={`${secondaryButton} mt-2`}
              onClick={() => onChange([...steps, { ...EMPTY_STEP }])}>
        <Plus className="h-4 w-4" aria-hidden />
        Add a step
      </button>
    </>
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
  const [editingDetails, setEditingDetails] = useState(false)

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
  const equipmentName = useEquipmentName(jsa.data?.equipment_id)

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
        {isDraft && mayWrite && !editingDetails && (
          <button type="button" className={secondaryButton} onClick={() => setEditingDetails(true)}>
            Edit details
          </button>
        )}
        <StatusPill status={j.status} />
      </PageHeader>

      {editingDetails && (
        <JsaDetailsForm jsa={j} onClose={() => setEditingDetails(false)} />
      )}
      <Card className={'mb-4' + (editingDetails ? ' hidden' : '')}>
        {j.job_description && (
          <p className="mb-2 text-sm text-neutral-700">{j.job_description}</p>
        )}
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
          <div>
            <dt className="text-xs text-neutral-500">PPE for the job</dt>
            <dd className="text-sm text-neutral-900">
              {(j.ppe_items ?? []).map((p) => p.label).join(', ')}
              {j.required_ppe && (
                <span className="block text-neutral-700">{j.required_ppe}</span>
              )}
              {!(j.ppe_items ?? []).length && !j.required_ppe && '—'}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-neutral-500">System</dt>
            <dd className="text-sm text-neutral-900">{j.system_label ?? '—'}</dd>
          </div>
          <div>
            <dt className="text-xs text-neutral-500">Equipment</dt>
            <dd className="text-sm text-neutral-900">{equipmentName ?? '—'}</dd>
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

      {/* ★ Round 12, JSA-06. */}
      <RecordPhotos type="jsa" ownerId={j.id} canAdd={mayWrite} />

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
  { jsa, steps, editable: canEdit, onSaved }:
  { jsa: Jsa; steps: Step[]; editable: boolean; onSaved: () => void },
) {
  const [draft, setDraft] = useState<Step[]>(steps.map(editable))
  const [dirty, setDirty] = useState(false)

  useEffect(() => { setDraft(steps.map(editable)); setDirty(false) }, [steps])

  const save = useMutation({
    mutationFn: () => api.put(`/api/v1/jsa/${jsa.id}/steps`, {
      steps: draft.filter((s) => s.step.trim()).map(stepBody),
    }),
    onSuccess: () => { setDirty(false); onSaved() },
  })

  if (!canEdit) {
    return (
      <>
        <SectionTitle className="mb-2">Steps</SectionTitle>
        {steps.length === 0 ? (
          <Empty>No steps written.</Empty>
        ) : (
          <ol className="flex flex-col gap-2">
            {steps.map((s, i) => {
              const hazards = [...(s.hazard_items ?? []).map((x) => x.label), s.hazards].filter(Boolean)
              const controls = [...(s.control_items ?? []).map((x) => x.label), s.controls].filter(Boolean)
              return (
                <li key={s.id ?? i}>
                  <Card>
                    <p className="text-sm font-medium text-neutral-900">
                      <span className="mr-2 font-mono text-xs text-neutral-400">{i + 1}</span>
                      {s.step}
                    </p>
                    {hazards.length > 0 && (
                      <p className="mt-1 text-xs text-danger-700">
                        <span className="font-semibold">Can go wrong:</span> {hazards.join('; ')}
                      </p>
                    )}
                    {controls.length > 0 && (
                      <p className="mt-0.5 text-xs text-neutral-700">
                        <span className="font-semibold">Controlled by:</span> {controls.join('; ')}
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
              )
            })}
          </ol>
        )}
      </>
    )
  }

  return (
    <>
      <SectionTitle className="mb-2">Steps</SectionTitle>
      <StepList steps={draft} onChange={(next) => { setDraft(next); setDirty(true) }} />
      <div className="mt-2 flex justify-end">
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
