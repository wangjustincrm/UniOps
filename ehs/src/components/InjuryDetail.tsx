/**
 * Section B — the injury detail HSE's medical form asks for.
 *
 * One component, used by the report form and by the investigation. It is the
 * same section on HSE's paper form, and two copies of a PHIPA-sensitive
 * questionnaire would drift: a field added on one screen and missing on the
 * other is how a record ends up incomplete depending on who filled it in.
 *
 * ★ The SAMPLE order — signs, allergies, medicines, past history, last meal,
 * events — is not a layout choice. It is the order a first aider is trained to
 * ask in, and re-sorting the screen makes them skip one.
 */
import { Plus, Trash2 } from 'lucide-react'
import { Field, inputClass, inputSized, SectionTitle } from './ui'

export interface VitalsReading {
  at: string; pulse: string; respirations: string; blood_pressure: string
  pain_level: number | null; pain_location: string; other: string
}

export const EMPTY_READING: VitalsReading = {
  at: '', pulse: '', respirations: '', blood_pressure: '',
  pain_level: null, pain_location: '', other: '',
}

export interface TimelineEntry { time: string; event: string }

/**
 * What this component edits — a subset of the full medical record.
 *
 * Every field is optional because the investigation holds this record as a
 * `Partial` while it loads: a record that has not arrived yet is not a record
 * whose allergies field is empty, and the two must not look alike here.
 */
export interface InjuryDetail {
  assessed_at_text?: string | null
  signs_symptoms?: string | null
  allergies?: string | null
  medicines?: string | null
  past_medical_history?: string | null
  last_meal?: string | null
  events_prior?: string | null
  general_comments?: string | null
  vitals_series?: VitalsReading[]
  timeline?: TimelineEntry[]
}

export const EMPTY_INJURY_DETAIL: InjuryDetail = {
  assessed_at_text: null, signs_symptoms: null, allergies: null, medicines: null,
  past_medical_history: null, last_meal: null, events_prior: null,
  general_comments: null, vitals_series: [], timeline: [],
}

const SAMPLE = [
  ['signs_symptoms', 'Signs and symptoms'],
  ['allergies', 'Allergies'],
  ['medicines', 'Medicines'],
  ['past_medical_history', 'Past medical history'],
  ['last_meal', 'Last meal'],
  ['events_prior', 'Events prior'],
  ['general_comments', 'General comments'],
] as const

/** True when anything has been entered — so an untouched section is not sent. */
export function hasInjuryDetail(d: InjuryDetail): boolean {
  if (d.assessed_at_text?.trim()) return true
  if (SAMPLE.some(([key]) => d[key]?.trim())) return true
  if ((d.vitals_series ?? []).some((v) => Object.values(v).some(
    (x) => x !== null && String(x).trim() !== ''))) return true
  return (d.timeline ?? []).some((t) => t.time.trim() || t.event.trim())
}

export function InjuryDetailFields<T extends InjuryDetail>(
  { value, onChange, showTimeline = true }: {
    value: T
    onChange: (next: T) => void
    /** The timeline is built while treatment is happening; a form filled in
     *  afterwards has one, a form filled in at the time usually does not. */
    showTimeline?: boolean
  }) {
  const set = (patch: Partial<InjuryDetail>) => onChange({ ...value, ...patch })

  return (
    <>
      <SectionTitle className="mb-2 mt-2">Medical information</SectionTitle>
      <Field label="Time of assessment">
        <input className={inputClass} value={value.assessed_at_text ?? ''}
               onChange={(e) => set({ assessed_at_text: e.target.value })}
               placeholder="10:00 am" />
      </Field>
      {SAMPLE.map(([key, label]) => (
        <Field key={key} label={label}>
          <textarea rows={2} className={`${inputClass} py-2`}
                    value={value[key] ?? ''}
                    onChange={(e) => set({ [key]: e.target.value } as Partial<InjuryDetail>)} />
        </Field>
      ))}

      <SectionTitle className="mb-2 mt-2">Vitals</SectionTitle>
      {/* Taken more than once, because the trend is what decides whether
          somebody goes to hospital: a pulse of 110 reads one way on its own
          and another beside the 90 from five minutes earlier. */}
      {(value.vitals_series ?? []).map((v, i) => {
        const setReading = (patch: Partial<VitalsReading>) =>
          set({ vitals_series: (value.vitals_series ?? []).map(
            (x, j) => (j === i ? { ...x, ...patch } : x)) })
        return (
          <div key={i} className="mb-3 rounded-lg border border-neutral-200 p-2.5">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-xs font-medium text-neutral-600">Reading {i + 1}</span>
              <button type="button"
                      onClick={() => set({
                        vitals_series: (value.vitals_series ?? []).filter((_, j) => j !== i) })}
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
              onClick={() => set({
                vitals_series: [...(value.vitals_series ?? []), { ...EMPTY_READING }] })}
              className="mb-2 flex min-h-[38px] items-center gap-1.5 text-sm font-medium text-primary-700">
        <Plus className="h-4 w-4" aria-hidden />
        {(value.vitals_series ?? []).length === 0 ? 'Record vitals' : 'Record vitals again'}
      </button>

      {showTimeline && (
        <>
          <SectionTitle className="mb-2 mt-2">Timeline</SectionTitle>
          {(value.timeline ?? []).map((t, i) => (
            // `min-w-0` on the event box is what stops this row being wider
            // than the panel: a flex item will not shrink past its content by
            // default, so the event text was pushed off the edge.
            <div key={i} className="mb-2 flex gap-2">
              <input type="time" className={`${inputSized('w-32')} shrink-0`} value={t.time}
                     aria-label="Time"
                     onChange={(e) => set({ timeline: (value.timeline ?? []).map(
                       (x, j) => j === i ? { ...x, time: e.target.value } : x) })} />
              <input className={`${inputClass} min-w-0`} value={t.event} placeholder="What happened"
                     onChange={(e) => set({ timeline: (value.timeline ?? []).map(
                       (x, j) => j === i ? { ...x, event: e.target.value } : x) })} />
              <button type="button"
                      onClick={() => set({
                        timeline: (value.timeline ?? []).filter((_, j) => j !== i) })}
                      className="flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400">
                <Trash2 className="h-4 w-4" aria-hidden />
                <span className="sr-only">Remove</span>
              </button>
            </div>
          ))}
          <button type="button"
                  onClick={() => set({
                    timeline: [...(value.timeline ?? []), { time: '', event: '' }] })}
                  className="flex min-h-[38px] items-center gap-1.5 text-sm font-medium text-primary-700">
            <Plus className="h-4 w-4" aria-hidden />
            Add a timeline entry
          </button>
        </>
      )}
    </>
  )
}
