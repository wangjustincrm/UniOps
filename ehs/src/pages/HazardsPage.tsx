/**
 * The hazard register: what is known to be dangerous, ranked worst first.
 *
 * Ranked, not listed. A register in creation order is a filing cabinet; the
 * only question anyone asks of it is "what is the worst thing we know about and
 * have not fixed", so that is the order and the top of the page says how many
 * sit at each rating.
 *
 * A rating cannot be typed in. It comes from HSE's matrix — likelihood against
 * severity, judged on a named consequence — because the matrix is the company's
 * document and a register that let a number be entered beside a hazard would
 * drift from it within a month.
 */
import EquipmentSelect, { EquipmentName } from '@/components/EquipmentSelect'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldAlert, Plus, X } from 'lucide-react'
import { api } from '@/lib/api'
import {
  CausesEditor, causesPayload, spendActionDrafts, unfinishedAction, type CauseDraft, type SavedAction,
} from '@/components/CausesEditor'
import { Pagination, SearchBox, useListView, DateRangeFilter } from '@/components/ListView'
import { formatDate, formatDateTime } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { useMyProfile } from '@/hooks/useMyProfile'
import { PhotoCapture } from '@/components/PhotoCapture'
import { RecordPhotos, uploadPhotos } from '@/components/RecordPhotos'
import { RiskAssessmentDialog } from '@/components/RiskAssessmentDialog'
import type { CompressedImage } from '@/lib/imageCompress'
import { LocationSelect } from '@/components/LocationSelect'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { VocabularyMultiSelect } from '@/components/VocabularyPicker'
import { RatingBadge, RiskScoringHistory } from '@/components/RiskScoring'
import {
  Card, Empty, ErrorNote, Field, inputClass, LoadFailedNote, PageHeader, primaryButton, secondaryButton, SectionTitle,
} from '@/components/ui'

export interface Hazard {
  id: string; hazard_no: string; title: string; description: string | null
  /** What was found when somebody went and looked. */
  findings: string | null
  hazard_type_id: string | null; hazard_type_label: string | null
  location_id: string | null; location_path: string | null; department_name: string | null
  equipment_id?: string | null
  risk_assessment_id: string | null
  current_rating: string | null; residual_rating: string | null
  existing_controls: string | null; control_level: string | null
  owner_id: string | null; owner_name: string | null
  identified_on: string | null; review_due_on: string | null
  status: string; created_at: string
  created_by_name?: string | null
  /** Round 12, HAZ-11: several kinds and controls, as named when picked. */
  hazard_types?: { id: string; label: string }[]
  hazard_controls?: { id: string; label: string }[]
  /** Kirk, round 12: "last revised on". An edit, not an assessment. */
  revised_at?: string | null; revised_by_name?: string | null
}
interface RatingCount { rating: string; count: number }

/** The kinds a hazard is, old single-kind hazards included. */
export function hazardKinds(h: Hazard): string[] {
  if (h.hazard_types?.length) return h.hazard_types.map((k) => k.label)
  return h.hazard_type_label ? [h.hazard_type_label] : []
}

export const CONTROL_LEVELS = [
  { key: 'elimination', label: 'Elimination' },
  { key: 'substitution', label: 'Substitution' },
  { key: 'engineering', label: 'Engineering control' },
  { key: 'administrative', label: 'Administrative control' },
  { key: 'ppe', label: 'PPE' },
]

export default function HazardsPage() {
  const { can } = usePermissions()
  const mayWrite = can('ehs.action.write')
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Hazard | null>(null)
  // "Can not view the risk register once created." The scoring was kept — the
  // likelihood, the severity, which consequence it was judged on, who judged
  // it and when — and the only thing that ever came back to the screen was a
  // coloured word. A rating with no working shown is not something an auditor
  // can take, and it is not something the next assessor can disagree with.
  const [scoringFor, setScoringFor] = useState<string | null>(null)
  const [assessing, setAssessing] = useState<Hazard | null>(null)
  const [showEliminated, setShowEliminated] = useState(false)
  // ★ Round 12, HAZ-07: newest first, worst first on request. Round 3 had
  // "ranked, not listed"; Kirk reversed it.
  const [order, setOrder] = useState<'newest' | 'risk'>('newest')

  const qs = new URLSearchParams({ order })
  if (!showEliminated) { qs.append('status', 'open'); qs.append('status', 'controlled') }
  // `list_hazards` caps at 300 with no parameter to raise it.
  const HAZARD_LIMIT = 300

  const hazards = useQuery({
    queryKey: ['hazards', showEliminated, order],
    queryFn: () => api.get<Hazard[]>(`/api/v1/hazards?${qs}`),
  })
  const summary = useQuery({
    queryKey: ['hazard-summary'],
    queryFn: () => api.get<RatingCount[]>('/api/v1/hazards/summary'),
  })
  const [search] = useSearchParams()
  const list = useListView(hazards.data, (h) => [
    h.hazard_no, h.title, h.description, h.location_path, h.owner_name,
    ...hazardKinds(h), h.current_rating, h.status, h.created_by_name,
    // `?q=` still lands an old link on its row; links now go to /hazards/:id.
  ], { dateOf: (h) => h.created_at, query: search.get('q') ?? '' })

  return (
    <div className="p-4">
      <PageHeader title="Hazard register"
                  subtitle="What is known to be dangerous here, and what is being done about it">
        {mayWrite && (
          <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            Record a hazard
          </button>
        )}
      </PageHeader>

      {(summary.data?.length ?? 0) > 0 && (
        <ul className="mb-4 flex flex-wrap gap-2">
          {summary.data?.map((r) => (
            <li key={r.rating}
                className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2.5">
              <p className="text-xs capitalize text-neutral-500">{r.rating}</p>
              <p className="font-mono text-lg font-bold tabular-nums text-neutral-900">
                {r.count}
              </p>
            </li>
          ))}
        </ul>
      )}

      {creating && <HazardForm onClose={() => setCreating(false)} />}
      {editing && (
        // ★ Keyed on the hazard. The form reads an existing hazard's causes once
        // and holds them in its own state; without a key, opening a second
        // hazard while one is open reuses the component and carries the first
        // one's causes across — and saving would then write them onto the
        // second. Raised in review by uniops-41.
        <HazardForm key={editing.id} existing={editing} onClose={() => setEditing(null)} />
      )}

      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <div role="group" aria-label="Order" className="inline-flex overflow-hidden rounded-lg border border-neutral-300">
          {([['newest', 'Newest first'], ['risk', 'Highest risk first']] as const).map(([key, text]) => (
            <button key={key} type="button" aria-pressed={order === key} onClick={() => setOrder(key)}
                    className={`min-h-[36px] px-3 text-xs font-medium ${order === key
                      ? 'bg-primary-600 text-white' : 'bg-white text-neutral-700'}`}>
              {text}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-xs text-neutral-600">
          <input type="checkbox" checked={showEliminated}
                 onChange={(e) => setShowEliminated(e.target.checked)} />
          Include hazards that have been eliminated
        </label>
      </div>

      <SearchBox value={list.query} onChange={list.setQuery}
                 placeholder="Search by number, what it is, area, owner or rating">
        <DateRangeFilter {...list.dateFilter} />
      </SearchBox>

      {hazards.data?.length === 0 && !creating ? (
        <Empty>
          Nothing on the register. A hazard recorded here is a standing fact
          about the plant — it stays until it is designed out, and comes back
          round for review while it has not been.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {list.pageRows.map((h) => (
            <li key={h.id}>
              <Card>
                <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5">
                  <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-neutral-400" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <Link to={`/hazards/${h.id}`}
                          className="block text-sm font-semibold text-neutral-900 hover:text-primary-700 hover:underline">
                      {h.title}
                    </Link>
                    <span className="mt-0.5 block text-xs text-neutral-500">
                      {h.hazard_no}
                      {h.department_name && ` · ${h.department_name}`}
                      {h.location_path && ` · ${h.location_path}`}<EquipmentName id={h.equipment_id} />
                      {h.owner_name && ` · owned by ${h.owner_name}`}
                    </span>
                    {/* Kirk, round 12: the date and time it was saved, just under
                        the number, department and owner. */}
                    <span className="mt-0.5 block text-xs text-neutral-500">
                      Recorded {formatDateTime(h.created_at)}
                      {h.created_by_name && ` by ${h.created_by_name}`}
                      {h.revised_at && ` · last revised on ${formatDate(h.revised_at)}`}
                    </span>
                    {hazardKinds(h).length > 0 && (
                      <span className="mt-1 flex flex-wrap gap-1">
                        {hazardKinds(h).map((k) => (
                          <span key={k} className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] text-neutral-700">{k}</span>
                        ))}
                      </span>
                    )}
                    {h.description && (
                      <span className="mt-1 block text-sm text-neutral-700">{h.description}</span>
                    )}
                    {(h.existing_controls || (h.hazard_controls ?? []).length > 0) && (
                      <span className="mt-1 block text-xs text-neutral-600">
                        <span className="font-medium">Controls:</span>{' '}
                        {[...(h.hazard_controls ?? []).map((c) => c.label), h.existing_controls]
                          .filter(Boolean).join('; ')}
                        {h.control_level && ` (${
                          CONTROL_LEVELS.find((c) => c.key === h.control_level)?.label
                          ?? h.control_level})`}
                      </span>
                    )}
                    {h.review_due_on && (
                      <span className="mt-0.5 block text-xs text-neutral-500">
                        Review due {formatDate(h.review_due_on)}
                      </span>
                    )}
                  </span>

                  <span className="flex shrink-0 flex-col items-end gap-1">
                    <RatingBadge rating={h.current_rating} />
                    {h.residual_rating && (
                      <span className="text-[11px] text-neutral-500">
                        after controls: <span className="capitalize">{h.residual_rating}</span>
                      </span>
                    )}
                    {h.status !== 'open' && (
                      <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold capitalize text-neutral-600">
                        {h.status}
                      </span>
                    )}
                    {mayWrite && (
                      <span className="flex items-center gap-2.5">
                        <button
                          type="button"
                          onClick={() => setScoringFor(scoringFor === h.id ? null : h.id)}
                          aria-expanded={scoringFor === h.id}
                          className="text-xs font-semibold text-neutral-500 hover:text-neutral-900 hover:underline"
                        >
                          Scoring
                        </button>
                        <button type="button" onClick={() => setEditing(h)}
                                className="text-xs font-semibold text-neutral-500 hover:text-neutral-900 hover:underline">
                          Edit
                        </button>
                        <button type="button" onClick={() => setAssessing(h)}
                                className="text-xs font-semibold text-primary-700 hover:underline">
                          {h.current_rating ? 'Reassess' : 'Assess the risk'}
                        </button>
                      </span>
                    )}
                  </span>
                </div>
                {scoringFor === h.id && (
                  <RiskScoringHistory sourceType="hazard" sourceId={h.id} showHazard
                    emptyNote="This hazard has not been scored against the matrix yet." />
                )}
              </Card>
            </li>
          ))}
        </ul>
      )}

      <Pagination page={list.page} pageCount={list.pageCount} total={list.total}
                  onPage={list.setPage} noun="hazards"
                  capped={(hazards.data ?? []).length >= HAZARD_LIMIT}
                  loaded={(hazards.data ?? []).length} />

      {assessing && (
        // ★ Round 12, HAZ-02: the incident's form, in hazard mode.
        <RiskAssessmentDialog sourceType="hazard" sourceId={assessing.id}
          onClose={() => setAssessing(null)}
          hazard={{ id: assessing.id, hazard_no: assessing.hazard_no, title: assessing.title,
                    kinds: hazardKinds(assessing) }} />
      )}
    </div>
  )
}

/**
 * Recording a hazard, and correcting one.
 *
 * A hazard on the register is a standing claim about the plant that outlives
 * whoever wrote it: the controls get improved, the owner changes job, the
 * review date moves. The PATCH endpoint existed from the start and nothing on
 * screen reached it, so the only way to fix a wrong owner or a superseded
 * control was to record the hazard a second time — which leaves the plant
 * reading as though it has two of them. One form, and `existing` decides
 * whether it records or corrects.
 */
export function HazardForm({ existing, onClose }: { existing?: Hazard; onClose: () => void }) {
  const queryClient = useQueryClient()
  const editing = Boolean(existing)
  const [title, setTitle] = useState(existing?.title ?? '')
  const [description, setDescription] = useState(existing?.description ?? '')
  // ★ Round 12, HAZ-11: several kinds and controls, from HSE's lists.
  const [kindIds, setKindIds] = useState<string[]>(
    existing?.hazard_types?.length ? existing.hazard_types.map((k) => k.id)
      : existing?.hazard_type_id ? [existing.hazard_type_id] : [])
  const [controlIds, setControlIds] = useState<string[]>(
    (existing?.hazard_controls ?? []).map((c) => c.id))
  const [newPhotos, setNewPhotos] = useState<CompressedImage[]>([])
  const me = useMyProfile()
  const [locationId, setLocationId] = useState<string | null>(existing?.location_id ?? null)
  const [equipmentId, setEquipmentId] = useState<string | null>(existing?.equipment_id ?? null)
  const [controls, setControls] = useState(existing?.existing_controls ?? '')
  const [level, setLevel] = useState(existing?.control_level ?? '')
  const [owner, setOwner] = useState<PersonValue>(existing?.owner_id
    ? { ...EMPTY_PERSON, userId: existing.owner_id, name: existing.owner_name ?? '' }
    : EMPTY_PERSON)
  // ★ Round 12, HAZ-01: "Owner — Defaults to you" said so and showed nothing
  // (the server filled it in on save). It now shows you, and stays editable.
  const [ownerTouched, setOwnerTouched] = useState(false)
  useEffect(() => {
    if (!existing && !ownerTouched && !owner.userId && me.data) {
      setOwner({ ...EMPTY_PERSON, userId: me.data.id, name: me.data.full_name })
    }
  }, [existing, ownerTouched, owner.userId, me.data])
  const [reviewOn, setReviewOn] = useState(existing?.review_due_on ?? '')
  const [findings, setFindings] = useState(existing?.findings ?? '')
  const [causes, setCauses] = useState<CauseDraft[]>([])
  // ★ Remembered once the hazard exists. Recording it is two calls — the hazard,
  // then its causes, because the causes need its id — and if the second fails,
  // pressing Record again must correct the hazard already made rather than make a
  // second one. The same shape as attaching a file on the document's create form.
  const [createdId, setCreatedId] = useState<string | null>(null)
  const hazardId = existing?.id ?? createdId

  // An existing hazard's causes, loaded once. Kept out of the form's own state
  // until they arrive so the editor does not open empty and then jump.
  const existingCauses = useQuery({
    queryKey: ['hazard', existing?.id, 'causes'],
    queryFn: () => api.get<{ immediate: CauseDraft[]; root: CauseDraft[]; actions: Record<string, SavedAction[]> }>(
      `/api/v1/hazards/${existing!.id}/causes`),
    enabled: Boolean(existing?.id),
  })
  const [causesLoaded, setCausesLoaded] = useState(false)
  if (!causesLoaded && existingCauses.data) {
    setCausesLoaded(true)
    const { immediate, root, actions } = existingCauses.data
    setCauses([...immediate, ...root].map((c) => ({ ...c, actions: c.id ? actions[c.id] : undefined })))
  }

  const body = () => ({
    title, description: description || null,
    findings: findings || null,
    hazard_type_ids: kindIds,
    hazard_control_ids: controlIds,
    location_id: locationId,
    equipment_id: equipmentId,
    existing_controls: controls || null,
    control_level: level || null,
    owner_id: owner.userId,
    review_due_on: reviewOn || null,
  })

  const create = useMutation({
    mutationFn: async () => {
      // ★ The hazard first, then what caused it — on the create form as well as
      // the edit one. Round 4 asked for exactly this on the investigation:
      // "having to save the record first and then edit the corrective action is
      // time wasting." Asking HSE to record a hazard, close it, and reopen it to
      // say why would be the same complaint a third time.
      let id = hazardId
      if (id) {
        await api.patch(`/api/v1/hazards/${id}`, body())
      } else {
        const made = await api.post<Hazard>('/api/v1/hazards', body())
        id = made.id
        setCreatedId(made.id)
      }
      await uploadPhotos('hazard', id, newPhotos)
      // ★ Never while an existing hazard's causes are unknown. Replacing is
      // what this endpoint does, so sending the empty set the editor shows
      // before they have loaded — or after the request for them failed — would
      // delete every cause on file that has no action on it. The same shape as
      // the inspection form overwriting answers it could not see.
      const causesKnown = !existing || causesLoaded
      if (causesKnown && (causesPayload(causes).length > 0 || existing)) {
        await api.put(`/api/v1/hazards/${id}/causes`, { causes: causesPayload(causes) })
        // Spent the moment that call returns. See `spendActionDrafts`.
        setCauses(spendActionDrafts)
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['hazards'] })
      queryClient.invalidateQueries({ queryKey: ['hazard-summary'] })
      // The hazard's own page, its causes and its change history.
      queryClient.invalidateQueries({ queryKey: ['hazard', hazardId] })
      queryClient.invalidateQueries({ queryKey: ['change-log'] })
      onClose()
    },
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-neutral-900">
            {editing ? `Correct ${existing!.hazard_no}` : 'Record a hazard'}
          </h2>
          {/* ★ Round 12, HAZ-01: who is recording it, shown and not editable. */}
          <p className="text-xs text-neutral-500">
            {editing
              ? `Recorded ${formatDateTime(existing!.created_at)}${existing!.created_by_name ? ` by ${existing!.created_by_name}` : ''}`
              : `Recorded by ${me.data?.full_name ?? 'you'}`}
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Field label="What is the hazard">
            <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)}
                   placeholder="Pinch point at the case sealer infeed" />
          </Field>
        </div>
        <Field label="Kind" hint="Pick every kind that applies">
          <VocabularyMultiSelect vocabulary="hazard" value={kindIds} onChange={(ids) => setKindIds(ids)}
                                 placeholder="Search the hazard list…" />
        </Field>
        <Field label="Where">
          <LocationSelect value={locationId} onChange={setLocationId} />
        </Field>
        {/* Round 10: the equipment register, as on the incident forms. */}
        <Field label="Equipment">
          <EquipmentSelect value={equipmentId} onChange={setEquipmentId} locationId={locationId} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Description">
            <textarea className={`${inputClass} min-h-[70px] py-2`} value={description}
                      onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
        {/* ★ Round 5, answer box 11: "the what was found … need to be added to
            the hazard register form." What was found when somebody went and
            looked, as against what the hazard is — an investigation records the
            same thing under "What happened". */}
        <div className="sm:col-span-2">
          <Field label="What was found">
            <textarea className={`${inputClass} min-h-[70px] py-2`} value={findings}
                      onChange={(e) => setFindings(e.target.value)}
                      placeholder="Guard removed for a changeover and never put back" />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="Controls in place" hint="Pick from the list; add anything it does not have below">
            <VocabularyMultiSelect vocabulary="hazard_control" value={controlIds}
                                   onChange={(ids) => setControlIds(ids)}
                                   placeholder="Search the hazard controls…" />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="Other controls, in words">
            <textarea className={`${inputClass} min-h-[60px] py-2`} value={controls}
                      onChange={(e) => setControls(e.target.value)} />
          </Field>
        </div>
        <Field label="Kind of control"
               hint="An auditor reads &ldquo;we gave them gloves&rdquo; differently from &ldquo;we removed the pinch point&rdquo;">
          <select className={inputClass} value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="">Not stated</option>
            {CONTROL_LEVELS.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
          </select>
        </Field>
        <Field label="Owner" hint="Defaults to you">
          <PersonPicker value={owner} onChange={(v) => { setOwnerTouched(true); setOwner(v) }}
                        allowExternal={false} />
        </Field>
        <Field label="Review due">
          <input type="date" className={inputClass} value={reviewOn}
                 onChange={(e) => setReviewOn(e.target.value)} />
        </Field>
      </div>

      {/* ★ Immediate and root causes, and the work raised against each root
          cause — the same editor the investigation uses, and the same
          "Raise a corrective action" fields, so a hazard and an incident are
          dealt with the same way. Round 5, answer box 11. */}
      <div className="mt-4 border-t border-neutral-100 pt-3">
        <SectionTitle className="mb-1.5">Immediate causes</SectionTitle>
        {existingCauses.isError ? (
          <>
            <LoadFailedNote what="The causes already recorded" />
            {/* ★ Said, because the save still works and it must be clear what
                it does. Sending the empty editor would replace the causes on
                file with nothing, so they are left alone. */}
            <p className="mt-1.5 text-xs text-neutral-600">
              Saving now changes the hazard itself and leaves its causes exactly as
              they are.
            </p>
          </>
        ) : (
          <CausesEditor type="immediate" vocabulary="immediate_cause"
                        help="The conditions present — what you could see."
                        causes={causes} onChange={setCauses} savedWith="the hazard" />
        )}
        <SectionTitle className="mb-1.5 mt-4">Root causes</SectionTitle>
        {!existingCauses.isError && (
          <CausesEditor type="root" vocabulary="root_cause"
                        help="Why those conditions existed. Each root cause should end up with a corrective action against it."
                        causes={causes} onChange={setCauses} savedWith="the hazard" />
        )}
      </div>

      {editing ? (
        <RecordPhotos type="hazard" ownerId={existing!.id} canAdd />
      ) : (
        <div className="mt-4 border-t border-neutral-100 pt-3">
          <SectionTitle className="mb-1.5">Photographs</SectionTitle>
          <PhotoCapture photos={newPhotos} onChange={setNewPhotos} />
        </div>
      )}

      <ErrorNote error={create.error} />
      {createdId && create.isError && (
        <p className="mt-2 text-xs text-neutral-600">
          The hazard itself was recorded. Pressing Record again saves its causes
          against it rather than recording it twice.
        </p>
      )}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!title.trim() || create.isPending || unfinishedAction(causes)
                          || (editing && !causesLoaded && !existingCauses.isError)}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending
            ? (editing ? 'Saving…' : 'Recording…')
            : (editing ? 'Save' : 'Record')}
        </button>
      </div>
    </Card>
  )
}
