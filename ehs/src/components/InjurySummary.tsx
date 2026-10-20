/**
 * The injury and the treatment, in one place, read only.
 *
 * Round 8 (open box): "when an incident is recorded add the following fields to
 * the right side under the person injured of the form. (Allow only the HSE role
 * to view this data)" — what happened, the kind of incident, where, the part of
 * the body and kind of injury, the treatment, anything owed afterwards, who gave
 * treatment, what else we should know, and everything in the medical section,
 * "all fields as read only".
 *
 * Shown only to holders of ehs.incident.medical.read (HSE and first aiders —
 * the people who enter it), the same gate as the medical record itself. Edited
 * where it always was: the report and the investigation.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { SectionTitle } from '@/components/ui'
import type { TimelineEntry, VitalsReading } from '@/components/InjuryDetail'

export interface SummaryPerson {
  id: string; role: string; person_name: string
  body_part_label: string | null; nature_of_injury_label: string | null
  treatment: string | null; sent_offsite?: boolean | null; follow_up?: string | null
}

interface Medical {
  supervisor_name: string | null; type_of_injury: string | null
  assessed_at_text: string | null; signs_symptoms: string | null
  allergies: string | null; medicines: string | null
  past_medical_history: string | null; last_meal: string | null
  events_prior: string | null; general_comments: string | null
  vitals_at_text: string | null; pulse: string | null; respirations: string | null
  blood_pressure: string | null; pain_level: number | null
  pain_location: string | null; vitals_other: string | null
  vitals_series: VitalsReading[]; timeline: TimelineEntry[]
  recorded_by_name: string | null
}

const SAMPLE: [keyof Medical, string][] = [
  ['assessed_at_text', 'Assessed at'],
  ['signs_symptoms', 'Signs and symptoms'],
  ['allergies', 'Allergies'],
  ['medicines', 'Medicines'],
  ['past_medical_history', 'Past medical history'],
  ['last_meal', 'Last meal'],
  ['events_prior', 'Events prior'],
  ['general_comments', 'General comments'],
]

function Item({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div className="border-b border-neutral-100 py-1.5 last:border-0">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">{label}</dt>
      <dd className="mt-0.5 whitespace-pre-wrap text-sm text-neutral-800">{value}</dd>
    </div>
  )
}

export function InjurySummary({ incidentId, title, kind, where, description, persons }: {
  incidentId: string
  title: string
  kind: string | null
  where: string | null
  description: string | null
  persons: SummaryPerson[]
}) {
  const medical = useQuery({
    queryKey: ['incident', incidentId, 'medical'],
    queryFn: () => api.get<Medical | null>(`/api/v1/incidents/${incidentId}/medical`),
    retry: false,
  })
  const m = medical.data
  const injured = persons.filter((p) => p.role === 'injured')
  const aiders = persons.filter((p) => p.role === 'first_aider').map((p) => p.person_name)
  // Older readings were single columns before a series was kept.
  const legacy = m && [m.pulse, m.respirations, m.blood_pressure, m.pain_level, m.pain_location,
                       m.vitals_other].some((x) => x !== null && x !== '')
  return (
    <section aria-label="Injury and medical summary" className="mb-4">
      <SectionTitle className="mb-2.5">Injury and medical summary</SectionTitle>
      <dl className="rounded-lg border border-neutral-200 bg-white px-3.5 py-1.5">
        <Item label="What happened" value={title} />
        <Item label="Kind of incident" value={kind} />
        <Item label="Where it happened" value={where ? where.split('/').join(' › ') : null} />
        {injured.map((p) => (
          <div key={p.id} className="border-b border-neutral-100 py-1.5 last:border-0">
            <dt className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">
              {p.person_name}
            </dt>
            <dd className="mt-0.5 text-sm text-neutral-800">
              <dl>
                <Item label="Part of the body"
                      value={p.body_part_label} />
                <Item label="Kind of injury"
                      value={p.nature_of_injury_label ?? (injured.length === 1 ? m?.type_of_injury : null)} />
                <Item label="Treatment given" value={p.treatment} />
                <Item label="Sent off site for treatment"
                      value={p.sent_offsite === true ? 'Yes' : p.sent_offsite === false ? 'No' : null} />
                <Item label="Anything owed afterwards" value={p.follow_up} />
              </dl>
            </dd>
          </div>
        ))}
        <Item label="Who gave treatment" value={aiders.join(', ')} />
        <Item label="What else we should know" value={description} />
      </dl>

      {medical.isError && (
        <p className="mt-2 text-xs text-neutral-500">The medical section could not be loaded.</p>
      )}
      {m && (
        <dl className="mt-2 rounded-lg border border-neutral-200 bg-white px-3.5 py-1.5">
          <Item label="Medical section recorded by" value={m.recorded_by_name} />
          <Item label="Supervisor told" value={m.supervisor_name} />
          {SAMPLE.map(([key, label]) => <Item key={key} label={label} value={m[key] as string | null} />)}
          {legacy && (
            <Item label={`Vitals${m.vitals_at_text ? ` at ${m.vitals_at_text}` : ''}`} value={
              [m.pulse && `Pulse ${m.pulse}`, m.respirations && `Respirations ${m.respirations}`,
               m.blood_pressure && `BP ${m.blood_pressure}`,
               m.pain_level !== null && `Pain ${m.pain_level}/10`, m.pain_location,
               m.vitals_other].filter(Boolean).join(' · ')} />
          )}
          {(m.vitals_series ?? []).map((v, i) => (
            <Item key={i} label={`Vitals${v.at ? ` at ${v.at}` : ` ${i + 1}`}`} value={
              [v.pulse && `Pulse ${v.pulse}`, v.respirations && `Respirations ${v.respirations}`,
               v.blood_pressure && `BP ${v.blood_pressure}`,
               v.pain_level !== null && `Pain ${v.pain_level}/10`, v.pain_location, v.other]
                .filter(Boolean).join(' · ')} />
          ))}
          {(m.timeline ?? []).length > 0 && (
            <Item label="Timeline" value={
              <ol className="flex flex-col gap-0.5">
                {m.timeline.map((t, i) => (
                  <li key={i}><span className="font-mono text-xs text-neutral-500">{t.time}</span> {t.event}</li>
                ))}
              </ol>} />
          )}
        </dl>
      )}
      {medical.isSuccess && !m && (
        <p className="mt-2 text-xs text-neutral-500">No medical section has been filled in.</p>
      )}
    </section>
  )
}
