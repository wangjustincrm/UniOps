/**
 * Investigating an incident: what was found, and what caused it.
 *
 * HSE requires an investigation on every injury and every equipment-damage
 * incident, and neither can be closed until HSE has reviewed it. The backend has
 * held the fields since Phase 1 started; this is the screen that fills them.
 *
 * Causes are edited as a set rather than one at a time, because that is how the
 * API replaces them and because a half-saved cause list reads as an investigation
 * that found less than it did. Each cause keeps a snapshot of its wording: the
 * vocabulary gets relabelled over the years, and a signed investigation has to
 * keep reading the way it was signed.
 */
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useTabTitle } from '@uniops/shell'
import { api, ApiError } from '@/lib/api'
import {
  PageHeader, SectionTitle,
} from '@/components/ui'
import { ehsRoutes } from '@/app/routes'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { SignaturePad } from '@/components/SignaturePad'
import { VocabularyMultiSelect, VocabularySelect, type VocabItem } from '@/components/VocabularyPicker'
import { useMyProfile } from '@/hooks/useMyProfile'

interface Investigation {
  id: string
  investigator_id: string | null
  investigator_name: string | null
  supporting_investigator_ids: string[]
  identified_hazard_ids: string[]
  ppe_that_could_prevent: string[]
  sequence_of_events: string | null
  root_cause_narrative: string | null
  completed_at: string | null
  signed_by_name: string | null
}

interface VitalsReading {
  at: string; pulse: string; respirations: string; blood_pressure: string
  pain_level: number | null; pain_location: string; other: string
}

const EMPTY_READING: VitalsReading = {
  at: '', pulse: '', respirations: '', blood_pressure: '',
  pain_level: null, pain_location: '', other: '',
}

interface Medical {
  supervisor_id: string | null; supervisor_name: string | null
  /** Superseded by the injured person's nature of injury, which is a list.
   *  Still read so an older record keeps showing what was typed into it. */
  type_of_injury: string | null
  assessed_at_text: string | null; signs_symptoms: string | null
  allergies: string | null; medicines: string | null
  past_medical_history: string | null; last_meal: string | null
  events_prior: string | null; general_comments: string | null
  vitals_at_text: string | null; pulse: string | null; respirations: string | null
  blood_pressure: string | null; pain_level: number | null
  pain_location: string | null; vitals_other: string | null
  timeline: { time: string; event: string }[]
  vitals_series: VitalsReading[]
}

interface Cause {
  id?: string
  cause_type: 'immediate' | 'root'
  vocabulary_item_id: string | null
  label: string
  note: string | null
}

interface CauseTree {
  incident_no: string
  immediate: Cause[]
  root: Cause[]
}

const inputClass =
  'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
  'focus:border-primary-400 focus:outline-none'

function Field({ label, help, children }: {
  label: string; help?: string; children: React.ReactNode
}) {
  return (
    <div className="mb-4">
      <label className="mb-1 block text-sm font-medium text-neutral-800">{label}</label>
      {help && <p className="mb-1.5 text-xs text-neutral-500">{help}</p>}
      {children}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-6 rounded-xl border border-neutral-200 bg-white p-4">
      <SectionTitle className="mb-3">{title}</SectionTitle>
      {children}
    </section>
  )
}

export default function InvestigationPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const profile = useMyProfile()

  const incident = useQuery({
    queryKey: ['incident', id],
    queryFn: () => api.get<{
      incident_no: string; title: string; form_kind: string
      persons: {
        id: string; role: string; person_name: string
        body_part_id: string | null; body_part_label: string | null
        nature_of_injury_id: string | null; nature_of_injury_label: string | null
      }[]
    }>(`/api/v1/incidents/${id}`),
  })
  const injuredPerson = incident.data?.persons?.find((p) => p.role === 'injured') ?? null
  const existing = useQuery({
    queryKey: ['incident', id, 'investigation'],
    queryFn: () => api.get<Investigation | null>(`/api/v1/incidents/${id}/investigation`),
  })
  // 403 for anyone without ehs.incident.medical.read — the section simply does
  // not render for them rather than showing an error they cannot act on.
  const medical = useQuery({
    queryKey: ['incident', id, 'medical'],
    queryFn: () => api.get<Medical | null>(`/api/v1/incidents/${id}/medical`),
    enabled: Boolean(id),
    retry: false,
  })

  const tree = useQuery({
    queryKey: ['incident', id, 'causes'],
    queryFn: () => api.get<CauseTree>(`/api/v1/incidents/${id}/cause-tree`),
  })

  useTabTitle(ehsRoutes, incident.data ? `${incident.data.incident_no} · Investigation` : undefined)

  const [lead, setLead] = useState<PersonValue>(EMPTY_PERSON)
  const [hazards, setHazards] = useState<string[]>([])
  const [ppe, setPpe] = useState<string[]>([])
  const [sequence, setSequence] = useState('')
  const [narrative, setNarrative] = useState('')
  const [causes, setCauses] = useState<Cause[]>([])
  const [signature, setSignature] = useState<string | null>(null)
  const [med, setMed] = useState<Partial<Medical>>({ timeline: [] })
  // What was recorded about the injury itself, which lives on the injured
  // person rather than in Section B. Seeded from the report and corrected
  // here — see the note on the PATCH above.
  const [bodyPart, setBodyPart] = useState<VocabItem | null>(null)
  const [natureOfInjury, setNatureOfInjury] = useState<VocabItem | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Load once. Re-running on every fetch would discard edits the moment a
  // background refetch resolved.
  const [loaded, setLoaded] = useState(false)
  // Seeded from what the reporter recorded, so opening the investigation and
  // saving it without touching these does not wipe them. Runs once per load:
  // once somebody has changed a picker, the record is not re-read over it.
  const [injurySeeded, setInjurySeeded] = useState(false)
  useEffect(() => {
    if (injurySeeded || !injuredPerson) return
    if (injuredPerson.body_part_id) {
      setBodyPart({ id: injuredPerson.body_part_id,
                    label: injuredPerson.body_part_label ?? '' } as VocabItem)
    }
    if (injuredPerson.nature_of_injury_id) {
      setNatureOfInjury({ id: injuredPerson.nature_of_injury_id,
                          label: injuredPerson.nature_of_injury_label ?? '' } as VocabItem)
    }
    setInjurySeeded(true)
  }, [injurySeeded, injuredPerson])

  useEffect(() => {
    if (loaded || existing.isLoading || tree.isLoading) return
    const inv = existing.data
    if (inv) {
      setLead(inv.investigator_id
        ? { userId: inv.investigator_id, name: inv.investigator_name ?? '',
            externalCompany: null, departmentId: null, departmentName: null }
        : EMPTY_PERSON)
      setHazards(inv.identified_hazard_ids ?? [])
      setPpe(inv.ppe_that_could_prevent ?? [])
      setSequence(inv.sequence_of_events ?? '')
      setNarrative(inv.root_cause_narrative ?? '')
    }
    const t = tree.data
    if (t) setCauses([...(t.immediate ?? []), ...(t.root ?? [])])
    if (medical.data) setMed({ ...medical.data, timeline: medical.data.timeline ?? [] })
    setLoaded(true)
  }, [loaded, existing.isLoading, existing.data, tree.isLoading, tree.data])

  // Section B is filled only where there was an injury, and only by someone the
  // medical permission covers — the query 403s for everyone else, and the
  // section then simply is not there.
  const showMedical =
    incident.data?.form_kind === 'medical' && !medical.isError

  const save = useMutation({
    mutationFn: async (opts: { sign: boolean }) => {
      await api.put(`/api/v1/incidents/${id}/investigation`, {
        investigator_id: lead.userId,
        supporting_investigator_ids: [],
        identified_hazard_ids: hazards,
        ppe_that_could_prevent: ppe,
        sequence_of_events: sequence || null,
        root_cause_narrative: narrative || null,
      })
      await api.put(`/api/v1/incidents/${id}/causes`, {
        causes: causes
          .filter((c) => c.label.trim())
          .map((c) => ({
            cause_type: c.cause_type,
            vocabulary_item_id: c.vocabulary_item_id,
            label: c.label,
            note: c.note || null,
          })),
      })
      if (showMedical) {
        // Built field by field rather than spread from what the GET returned:
        // the response carries its own id, incident id and recorded-by name,
        // and the endpoint now refuses fields it does not recognise — which
        // is the protection that stops a misspelled key reading as "cleared".
        await api.put(`/api/v1/incidents/${id}/medical`, {
          supervisor_id: med.supervisor_id ?? null,
          supervisor_name: med.supervisor_name ?? null,
          type_of_injury: med.type_of_injury ?? null,
          assessed_at_text: med.assessed_at_text ?? null,
          signs_symptoms: med.signs_symptoms ?? null,
          allergies: med.allergies ?? null,
          medicines: med.medicines ?? null,
          past_medical_history: med.past_medical_history ?? null,
          last_meal: med.last_meal ?? null,
          events_prior: med.events_prior ?? null,
          general_comments: med.general_comments ?? null,
          vitals_at_text: med.vitals_at_text ?? null,
          pulse: med.pulse ?? null,
          respirations: med.respirations ?? null,
          blood_pressure: med.blood_pressure ?? null,
          pain_level: med.pain_level ?? null,
          pain_location: med.pain_location ?? null,
          vitals_other: med.vitals_other ?? null,
          timeline: (med.timeline ?? []).filter((t) => t.time || t.event),
          vitals_series: med.vitals_series ?? [],
        })
      }
      if (showMedical && injuredPerson && (bodyPart || natureOfInjury)) {
        // The reporter names the body part from memory, often within minutes.
        // Whoever assesses the injury is who knows, and that is this step.
        await api.patch(`/api/v1/incidents/${id}/persons/${injuredPerson.id}`, {
          body_part_id: bodyPart?.id ?? null,
          body_part_label: bodyPart?.label ?? null,
          nature_of_injury_id: natureOfInjury?.id ?? null,
          nature_of_injury_label: natureOfInjury?.label ?? null,
        })
      }
      if (opts.sign && signature) {
        await api.post(`/api/v1/incidents/${id}/investigation/sign`, { signature })
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['incident', id] })
      navigate(`/incidents/${id}`)
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  if (incident.isLoading || existing.isLoading) {
    return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  }
  if (incident.isError) {
    return <p className="p-4 text-sm text-danger-700">{(incident.error as ApiError).message}</p>
  }

  const addCause = (type: 'immediate' | 'root') =>
    setCauses((c) => [...c, { cause_type: type, vocabulary_item_id: null, label: '', note: null }])

  const updateCause = (i: number, patch: Partial<Cause>) =>
    setCauses((c) => c.map((x, j) => (j === i ? { ...x, ...patch } : x)))

  const removeCause = (i: number) => setCauses((c) => c.filter((_, j) => j !== i))

  const causeRows = (type: 'immediate' | 'root', vocabulary: string, help: string) => (
    <>
      <p className="mb-2 text-xs text-neutral-500">{help}</p>
      {causes.map((c, i) => c.cause_type !== type ? null : (
        <div key={i} className="mb-2 rounded-lg border border-neutral-200 p-2.5">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <VocabularySelect
                vocabulary={vocabulary}
                value={c.vocabulary_item_id}
                onChange={(item: VocabItem | null) => updateCause(i, {
                  vocabulary_item_id: item?.id ?? null,
                  // Snapshot the wording, per the module's rule for every
                  // vocabulary reference on a record that gets signed.
                  label: item ? item.label : c.label,
                })}
              />
            </div>
            <button type="button" onClick={() => removeCause(i)}
                    className="flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400 hover:text-danger-600">
              <Trash2 className="h-4 w-4" aria-hidden />
              <span className="sr-only">Remove</span>
            </button>
          </div>
          <input
            value={c.note ?? ''}
            onChange={(e) => updateCause(i, { note: e.target.value })}
            placeholder="Note (optional)"
            className="mt-2 min-h-[38px] w-full rounded-md border border-neutral-200 px-2.5 text-sm focus:border-primary-400 focus:outline-none"
          />
        </div>
      ))}
      <button type="button" onClick={() => addCause(type)}
              className="flex min-h-[38px] items-center gap-1.5 text-sm font-medium text-primary-700">
        <Plus className="h-4 w-4" aria-hidden />
        Add {type === 'immediate' ? 'an immediate cause' : 'a root cause'}
      </button>
    </>
  )

  const rootCount = causes.filter((c) => c.cause_type === 'root' && c.label.trim()).length

  return (
    <div className="p-4 pb-24">
      <header className="mb-4">
        <p className="font-mono text-xs text-neutral-500">{incident.data?.incident_no}</p>
      <PageHeader title="Investigation" />
        <p className="mt-0.5 text-sm text-neutral-600">{incident.data?.title}</p>
      </header>

      {existing.data?.completed_at && (
        <p className="mb-4 rounded-lg bg-primary-50 px-3 py-2 text-xs text-primary-800">
          Signed by {existing.data.signed_by_name ?? 'the investigator'}. Saving again replaces
          the findings and requires a new signature.
        </p>
      )}

      <Section title="Who investigated">
        <Field label="Lead investigator">
          <PersonPicker value={lead} onChange={setLead} />
        </Field>
      </Section>

      <Section title="What was found">
        <Field label="Hazards identified"
               help="From your hazard list. These are what trend reports group by.">
          <VocabularyMultiSelect vocabulary="hazard" value={hazards} onChange={setHazards}
                                 placeholder="Search hazards…" />
        </Field>
        <Field label="PPE that could have prevented this">
          <VocabularyMultiSelect vocabulary="ppe" value={ppe} onChange={setPpe}
                                 placeholder="Search PPE…" />
        </Field>
        <Field
          label="What happened"
          help="Before, during and after — including equipment, materials and environmental conditions."
        >
          <textarea rows={5} value={sequence} onChange={(e) => setSequence(e.target.value)}
                    className={`${inputClass} py-2`} />
        </Field>
      </Section>

      <Section title="Immediate causes">
        {causeRows('immediate', 'immediate_cause',
                   'The conditions present at the time — what you could see.')}
      </Section>

      <Section title="Root causes">
        {causeRows('root', 'root_cause',
                   'Why those conditions existed. Each root cause should end up with a corrective action against it.')}
        <div className="mt-3">
          <Field label="Root cause narrative">
            <textarea rows={4} value={narrative} onChange={(e) => setNarrative(e.target.value)}
                      className={`${inputClass} py-2`} />
          </Field>
        </div>
        {rootCount === 0 && (
          <p className="rounded-lg bg-warning-50 px-3 py-2 text-xs text-warning-700">
            No root cause recorded. An auditor reads an investigation without one as unfinished.
          </p>
        )}
      </Section>

      {showMedical && (
        <Section title="Section B — injury detail (required by WSIB)">
          <Field label="Supervisor"
                 help="The follow-up goes to this person, so it has to be a name the system knows.">
            <PersonPicker
              value={med.supervisor_id || med.supervisor_name
                ? { ...EMPTY_PERSON, userId: med.supervisor_id ?? null,
                    name: med.supervisor_name ?? '' }
                : EMPTY_PERSON}
              onChange={(v) => setMed({
                ...med, supervisor_id: v.userId, supervisor_name: v.name || null })}
              allowExternal={false}
            />
          </Field>

          {/* Recorded against the injured person, not Section B: the injury
              register and WSIB Form 7 both read them from there, and a second
              copy typed here would be a second answer to the same question.
              Free text until now, which is why the body-part figure could not
              be grouped. */}
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="What part of the body?">
              <VocabularySelect vocabulary="body_part" value={bodyPart?.id ?? null}
                                onChange={setBodyPart} />
            </Field>
            <Field label="What kind of injury?">
              <VocabularySelect vocabulary="nature_of_injury"
                                value={natureOfInjury?.id ?? null}
                                onChange={setNatureOfInjury} />
            </Field>
          </div>

          {med.type_of_injury && (
            <p className="mb-2 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
              Typed on this record before these became lists:
              <span className="font-medium"> {med.type_of_injury}</span>
            </p>
          )}

          {/* SAMPLE: signs, allergies, medicines, past history, last meal,
              events. This is the order a first aider is trained to ask in —
              re-sorting the screen makes them skip one. */}
          <SectionTitle className="mb-2 mt-2">Medical information</SectionTitle>
          <Field label="Time of assessment">
            <input className={inputClass} value={med.assessed_at_text ?? ''}
                   onChange={(e) => setMed({ ...med, assessed_at_text: e.target.value })}
                   placeholder="10:00 am" />
          </Field>
          {([
            ['signs_symptoms', 'Signs and symptoms'],
            ['allergies', 'Allergies'],
            ['medicines', 'Medicines'],
            ['past_medical_history', 'Past medical history'],
            ['last_meal', 'Last meal'],
            ['events_prior', 'Events prior'],
            ['general_comments', 'General comments'],
          ] as const).map(([key, label]) => (
            <Field key={key} label={label}>
              <textarea rows={2} className={`${inputClass} py-2`}
                        value={(med[key] as string | null) ?? ''}
                        onChange={(e) => setMed({ ...med, [key]: e.target.value })} />
            </Field>
          ))}

          <SectionTitle className="mb-2 mt-2">Vitals</SectionTitle>
          {/* Taken more than once, because the trend is what decides whether
              somebody goes to hospital: a pulse of 110 reads one way on its own
              and another beside the 90 from five minutes earlier. The form used
              to hold exactly one set. */}
          {(med.vitals_series ?? []).map((v, i) => {
            const setReading = (patch: Partial<VitalsReading>) =>
              setMed({ ...med, vitals_series: (med.vitals_series ?? []).map(
                (x, j) => (j === i ? { ...x, ...patch } : x)) })
            return (
              <div key={i} className="mb-3 rounded-lg border border-neutral-200 p-2.5">
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-xs font-medium text-neutral-600">
                    Reading {i + 1}
                  </span>
                  <button type="button"
                          onClick={() => setMed({ ...med,
                            vitals_series: (med.vitals_series ?? []).filter((_, j) => j !== i) })}
                          className="flex min-h-[32px] min-w-[32px] items-center justify-center text-neutral-400">
                    <Trash2 className="h-4 w-4" aria-hidden />
                    <span className="sr-only">Remove reading {i + 1}</span>
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Time">
                    {/* A picker, not free text: these are compared against each
                        other and against the timeline, and "10:06 am" beside
                        "1006" cannot be. */}
                    <input type="time" className={inputClass} value={v.at}
                           onChange={(e) => setReading({ at: e.target.value })} />
                  </Field>
                  <Field label="Pulse"><input className={inputClass} value={v.pulse}
                    onChange={(e) => setReading({ pulse: e.target.value })} /></Field>
                  <Field label="Respirations"><input className={inputClass} value={v.respirations}
                    onChange={(e) => setReading({ respirations: e.target.value })} /></Field>
                  <Field label="Blood pressure"><input className={inputClass} value={v.blood_pressure}
                    onChange={(e) => setReading({ blood_pressure: e.target.value })} /></Field>
                  <Field label="Pain level (0–10)">
                    <input type="number" min={0} max={10} className={inputClass}
                           value={v.pain_level ?? ''}
                           onChange={(e) => setReading({
                             pain_level: e.target.value === '' ? null : Number(e.target.value) })} />
                  </Field>
                  <Field label="Where is the pain"><input className={inputClass} value={v.pain_location}
                    onChange={(e) => setReading({ pain_location: e.target.value })} /></Field>
                  <div className="col-span-2">
                    <Field label="Anything else observed">
                      <input className={inputClass} value={v.other}
                             onChange={(e) => setReading({ other: e.target.value })} />
                    </Field>
                  </div>
                </div>
              </div>
            )
          })}
          <button type="button"
                  onClick={() => setMed({ ...med,
                    vitals_series: [...(med.vitals_series ?? []), { ...EMPTY_READING }] })}
                  className="mb-2 flex min-h-[38px] items-center gap-1.5 text-sm font-medium text-primary-700">
            <Plus className="h-4 w-4" aria-hidden />
            {(med.vitals_series ?? []).length === 0 ? 'Record vitals' : 'Record vitals again'}
          </button>

          <SectionTitle className="mb-2 mt-2">Timeline</SectionTitle>
          {(med.timeline ?? []).map((t, i) => (
            // `min-w-0` on the event box below is what stops this row being
            // wider than the panel: a flex item will not shrink past its
            // content by default, so the event text was pushed off the edge.
            <div key={i} className="mb-2 flex gap-2">
              <input type="time" className={`${inputClass} w-32 shrink-0`} value={t.time}
                     aria-label="Time"
                     onChange={(e) => setMed({ ...med, timeline: (med.timeline ?? []).map(
                       (x, j) => j === i ? { ...x, time: e.target.value } : x) })} />
              <input className={`${inputClass} min-w-0`} value={t.event} placeholder="What happened"
                     onChange={(e) => setMed({ ...med, timeline: (med.timeline ?? []).map(
                       (x, j) => j === i ? { ...x, event: e.target.value } : x) })} />
              <button type="button"
                      onClick={() => setMed({ ...med,
                        timeline: (med.timeline ?? []).filter((_, j) => j !== i) })}
                      className="flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400">
                <Trash2 className="h-4 w-4" aria-hidden />
                <span className="sr-only">Remove</span>
              </button>
            </div>
          ))}
          <button type="button"
                  onClick={() => setMed({ ...med,
                    timeline: [...(med.timeline ?? []), { time: '', event: '' }] })}
                  className="flex min-h-[38px] items-center gap-1.5 text-sm font-medium text-primary-700">
            <Plus className="h-4 w-4" aria-hidden />
            Add a timeline entry
          </button>
        </Section>
      )}

      <Section title="Sign off">
        <SignaturePad value={signature} onChange={setSignature}
                      savedSignature={profile.data?.signature_image ?? null}
                      label="Sign to complete the investigation" />
      </Section>

      {error && (
        <p className="mb-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => navigate(`/incidents/${id}`)}
          className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 px-4 text-sm font-medium text-neutral-700"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={save.isPending}
          onClick={() => { setError(null); save.mutate({ sign: false }) }}
          className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 px-4 text-sm font-medium text-neutral-700 disabled:opacity-50"
        >
          Save
        </button>
        <button
          type="button"
          disabled={save.isPending || !signature}
          onClick={() => { setError(null); save.mutate({ sign: true }) }}
          className="min-h-[44px] flex-[1.4] rounded-lg bg-primary-600 px-4 text-sm font-semibold text-white disabled:opacity-50"
        >
          {save.isPending ? 'Saving…' : 'Save and sign'}
        </button>
      </div>
    </div>
  )
}
