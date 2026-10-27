/**
 * Reporting an incident.
 *
 * The entry point for the whole module and the one screen a production worker
 * may ever use, so it opens with three plainly worded choices rather than the
 * names of the forms behind them, and everything after that is one column of
 * 44px targets.
 *
 * The form saves a draft as it is filled. The plant has Wi-Fi dead zones and
 * the rule is simple: somebody who has finished typing has never lost their
 * work.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, Eye, Wrench } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { nowForInput, todayForInput } from '@/lib/formatDate'
import { PageHeader } from '@/components/ui'
import { discardDraft, loadDraft, saveDraft } from '@/lib/draftStore'
import type { CompressedImage } from '@/lib/imageCompress'
import { LocationSelect } from '@/components/LocationSelect'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { PhotoCapture } from '@/components/PhotoCapture'
import { SignaturePad } from '@/components/SignaturePad'
import { VocabularySelect, type VocabItem } from '@/components/VocabularyPicker'
import { useMyProfile } from '@/hooks/useMyProfile'
import { usePermissions } from '@/hooks/usePermissions'
import {
  EMPTY_INJURY_DETAIL, hasInjuryDetail, InjuryDetailFields, type InjuryDetail,
} from '@/components/InjuryDetail'

type Kind = 'medical' | 'equipment' | 'near_miss'

const KINDS: { kind: Kind; label: string; hint: string; Icon: typeof AlertTriangle; tone: string }[] = [
  {
    kind: 'medical',
    label: 'Someone was hurt',
    hint: 'Injury or illness — first aid through lost time',
    Icon: AlertTriangle,
    tone: 'bg-danger-50 text-danger-600',
  },
  {
    kind: 'equipment',
    label: 'Equipment was involved',
    hint: 'Damage, failure or an unsafe condition',
    Icon: Wrench,
    tone: 'bg-warning-50 text-warning-700',
  },
  {
    kind: 'near_miss',
    label: 'Near miss or good catch',
    hint: 'Nobody was hurt — but they could have been',
    Icon: Eye,
    tone: 'bg-primary-50 text-primary-700',
  },
]

interface IncidentTypeOption {
  code: string
  label: string
  form_kind: string
  /** Fatality and Unconscious are MOL-reportable and say nothing about lost
   *  time, so the form asks for the injury class separately. */
  asks_injury_class: boolean
  /** What the injury picker opens on for this type. */
  default_injury_class: string | null
  /** Whether somebody was actually hurt, and so whether the WSIB detail is
   *  worth asking for here. Served rather than decided on this side, for the
   *  same reason the list of types is. */
  injury_detail: boolean
}

interface FormState {
  kind: Kind | null
  title: string
  date: string
  time: string
  locationId: string
  incidentType: string
  injuryClass: string
  description: string
  injured: PersonValue
  /** The first aider who treated them — Regulation 1101 wants the name. */
  firstAider: PersonValue
  // Two vocabularies the reporting form has never offered, though both were
  // populated in Settings and accepted by the API from the start. Nothing has
  // ever written them, which is why the body-part chart on the dashboard can
  // only ever be empty and the WSIB pre-fill is missing two of its required
  // fields. (The incident category is not here on purpose: the server derives
  // it from the twelve incident types, and a second place to set it would be
  // a second answer to the same question.)
  bodyPart: VocabItem | null
  natureOfInjury: VocabItem | null
  // What the separate first aid register used to hold. It is the same event,
  // and two places to record it meant the same cut finger was entered twice —
  // or once, in whichever the person happened to open.
  treatment: string
  sentOffsite: boolean
  followUp: string
  talkedToOperator: '' | 'yes' | 'no' | 'na'
  whyNot: string
  // Section B on HSE's form — SAMPLE and vitals, filled in by whoever assessed
  // the injury. It used to live only on the investigation, which is a screen
  // opened hours later: by then the pulse and the pain score are being written
  // from memory, if they are written at all.
  injury: InjuryDetail
  anonymous: boolean
  signature: string | null
}

/**
 * A blank form, built when one is asked for.
 *
 * ★ A function, not a constant. The date and the time are filled in rather
 * than left blank — an incident is reported now, about now, and those were the
 * two most-typed fields on the form. But this shell keeps tabs alive: a
 * constant would have been computed once when the module loaded, so a tab
 * opened in the morning and filled in after lunch would carry the morning's
 * time, a tab left open over midnight would carry yesterday's date, and
 * Discard would restore whichever moment the page happened to load at.
 *
 * Both stay editable: a report written up the next morning says so.
 */
const emptyForm = (): FormState => ({
  kind: null, title: '', date: todayForInput(), time: nowForInput(),
  locationId: '', incidentType: '',
  injuryClass: '', description: '',
  injured: EMPTY_PERSON, firstAider: EMPTY_PERSON, bodyPart: null, natureOfInjury: null,
  treatment: '', sentOffsite: false, followUp: '',
  talkedToOperator: '', whyNot: '', anonymous: false, signature: null,
  injury: { ...EMPTY_INJURY_DETAIL },
})

const DRAFT_KIND = 'incident'
const DRAFT_ID = 'current'

export default function IncidentReportPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const profile = useMyProfile()
  const [form, setForm] = useState<FormState>(emptyForm)
  const [photos, setPhotos] = useState<CompressedImage[]>([])
  const [restored, setRestored] = useState(false)
  const [draftWarning, setDraftWarning] = useState<string | null>(null)

  // Restore whatever was being typed when the page was last left.
  useEffect(() => {
    const draft = loadDraft<{ form: FormState; photos: CompressedImage[] }>(DRAFT_KIND, DRAFT_ID)
    if (draft) {
      // Merge over the empty form rather than replacing it. The version check
      // in loadDraft already rejects a draft from an older shape, and this is
      // the second guard: a field added since the draft was written must come
      // back as its default, not as undefined for the page to read through.
      setForm({ ...emptyForm(), ...draft.data.form,
                injured: draft.data.form?.injured ?? EMPTY_PERSON,
                firstAider: draft.data.form?.firstAider ?? EMPTY_PERSON,
                // Never restored, because it is never saved (see below).
                injury: { ...EMPTY_INJURY_DETAIL } })
      setPhotos(draft.data.photos ?? [])
      setRestored(true)
    }
  }, [])

  // Save on every change once something has actually been entered.
  //
  // ★ Section B is deliberately left out of the draft. A draft lives in this
  // browser's storage, the plant's phones and tablets are shared, and signing
  // out does not empty it — so a half-finished form would leave somebody's
  // allergies and medication on a device the next person logs into, readable
  // to anyone who opens the developer tools whether or not the screen shows
  // it. Losing a few minutes of Section B if the page is closed is the right
  // side of that trade; the rest of the form still survives a dead zone.
  useEffect(() => {
    if (!form.kind) return
    const ok = saveDraft(DRAFT_KIND, DRAFT_ID,
                         { form: { ...form, injury: { ...EMPTY_INJURY_DETAIL } }, photos })
    setDraftWarning(ok ? null
      : 'This draft is too large to keep on the phone — submit it while you have signal.')
  }, [form, photos])

  const { data: incidentTypes } = useQuery({
    queryKey: ['incident-types', form.kind],
    queryFn: () => api.get<IncidentTypeOption[]>(
      `/api/v1/incidents/types?form_kind=${form.kind}`),
    enabled: Boolean(form.kind),
  })


  // HSE's own wording for the lost-time test, shown beside the choice it
  // decides. Fetched only once the form knows it will ask.
  // ★ Its own endpoint, not /settings/config — that one needs permission to
  // browse the incident register, which an ordinary employee does not have and
  // they are exactly who fills this in. Read from the config endpoint the
  // guidance would have been invisible to everybody who needs it.
  const { data: guidance } = useQuery({
    queryKey: ['reporting-guidance'],
    queryFn: () => api.get<{ lost_time_guidance: string | null }>(
      '/api/v1/settings/reporting-guidance'),
    staleTime: 10 * 60_000,
  })
  const lostTimeGuidance = guidance?.lost_time_guidance?.trim() || null

  const selectedType = incidentTypes?.find((t) => t.code === form.incidentType)
  const asksInjuryClass = Boolean(selectedType?.asks_injury_class)

  // ★ Section B is shown to the people allowed to hold it, and only for the
  // types where somebody was hurt. It carries allergies, medication and past
  // medical history — the same permission the investigation asks for, because
  // the record does not become less private for being typed earlier.
  const { can } = usePermissions()
  const showInjuryDetail = form.kind === 'medical' && Boolean(selectedType?.injury_detail)
    && can('ehs.incident.medical.read')
  // Somebody is named as hurt — picked from the directory or written in.
  const namedInjured = Boolean(form.injured.userId || form.injured.name.trim())

  const submit = useMutation({
    mutationFn: async () => {
      // Date and time are separate fields, as they are on the paper form.
      // Combined without a Z they parse as local time, which is what the
      // reporter meant, and toISOString then carries the offset the statutory
      // clocks need.
      const occurredAt = new Date(`${form.date}T${form.time || '00:00'}`).toISOString()
      const body: Record<string, unknown> = {
        form_kind: form.kind,
        title: form.title,
        occurred_at: occurredAt,
        location_id: form.locationId || null,
        description: form.description || null,
        is_anonymous: form.anonymous,
        signature: form.signature,
        incident_type: form.incidentType || null,
        // 'none' is this screen's way of saying nobody was hurt, which the
        // record holds as no injury class at all.
        // ★ 'none' is sent through rather than turned into null. The server
        // falls back to what the incident type implies when nothing was
        // answered, so a null here made "Nobody was hurt" on a First Aid Injury
        // store `first_aid` anyway, silently. Chosen and unanswered are
        // different facts; only the second one may be overridden.
        injury_class: form.injuryClass || null,
      }
      if (form.kind === 'medical' && form.injured.name.trim()) {
        // An employee match carries a user id; a written-in name does not, and
        // keeps whatever company was given. The record tells the two apart.
        body.persons = [{
          role: 'injured',
          person_name: form.injured.name.trim(),
          user_id: form.injured.userId,
          external_company: form.injured.externalCompany,
          department_id: form.injured.departmentId,
          body_part_id: form.bodyPart?.id ?? null,
          body_part_label: form.bodyPart?.label ?? null,
          nature_of_injury_id: form.natureOfInjury?.id ?? null,
          nature_of_injury_label: form.natureOfInjury?.label ?? null,
          treatment: form.treatment || null,
          sent_offsite: form.sentOffsite,
          follow_up: form.followUp || null,
        }]
        // The department of the person hurt is the department of the incident.
        // HSE asked for this so nobody picks a name and then picks the
        // department that name already implies.
        if (form.injured.departmentId) body.department_id = form.injured.departmentId
      }
      // ★ A row of its own rather than a column on the injured person: more than
      // one responder is normal, and the register answers "who treated this"
      // with all of them.
      // ★ Review (uniops-8b): only while somebody is named as hurt — the
      // treatment block, first aider included, is hidden once the injured
      // person is cleared, and a hidden answer is not one the reporter gave.
      if (form.kind === 'medical' && namedInjured && form.firstAider.name.trim()) {
        body.persons = [...((body.persons as unknown[]) ?? []), {
          role: 'first_aider',
          person_name: form.firstAider.name.trim(),
          user_id: form.firstAider.userId,
          external_company: form.firstAider.externalCompany,
        }]
      }
      if (form.kind === 'near_miss') {
        body.extra = {
          talked_to_operator: form.talkedToOperator || null,
          why_not: form.talkedToOperator === 'no' ? form.whyNot : null,
        }
      }
      const created = await api.post<{ id: string }>('/api/v1/incidents', body)
      await api.post(`/api/v1/incidents/${created.id}/submit`)

      // Section B goes up after the submit, because the endpoint refuses a
      // draft: the injury detail belongs to a report that exists. Only when
      // something was actually entered — a PUT of empty fields would create a
      // medical record saying nothing, with somebody's name against it.
      //
      // ★ A failure here does not fail the submit. The incident already
      // exists; throwing would leave the form on screen with the Submit button
      // live, and the next press would file the same injury a second time.
      // A duplicate statutory report is barely better than a missing one, so
      // this reports what did not save and says where to finish it.
      let injuryDetailFailed = false
      if (showInjuryDetail && hasInjuryDetail(form.injury)) {
        try {
          // The whole section, and only the keys the endpoint knows: it rejects
          // anything else outright rather than dropping it, which is what stops
          // a renamed field from looking like a cleared one.
          await api.put(`/api/v1/incidents/${created.id}/medical`, { ...form.injury })
        } catch {
          injuryDetailFailed = true
        }
      }

      // Photos go up after the incident exists, because they hang off its id.
      // A failure here is reported but does not fail the submit: the report is
      // already filed, and telling someone their injury report failed because
      // a photo did not upload would be both false and alarming.
      let photosFailed = 0
      for (const photo of photos) {
        try {
          const blob = await (await fetch(photo.dataUrl)).blob()
          const form = new FormData()
          form.append('file', blob, photo.name)
          await api.upload(`/api/v1/incidents/${created.id}/attachments`, form)
        } catch {
          photosFailed += 1
        }
      }
      return { ...created, photosFailed, injuryDetailFailed }
    },
    onSuccess: (created) => {
      discardDraft(DRAFT_KIND, DRAFT_ID)
      queryClient.invalidateQueries({ queryKey: ['incidents'] })
      if (created.photosFailed > 0) {
        window.alert(
          `Your report was filed as INC. ${created.photosFailed} of ${photos.length} ` +
          `photo(s) could not be uploaded — open the report and add them again.`,
        )
      }
      if (created.injuryDetailFailed) {
        window.alert(
          'Your report was filed, but the injury detail did not save. ' +
          'Open the report and enter it on the investigation — nothing else was lost.',
        )
      }
      navigate(`/incidents/${created.id}`)
    },
  })


  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  // ── Step 1: what happened ────────────────────────────────────────────────
  if (!form.kind) {
    return (
      <div className="flex flex-col gap-2 p-4">
      <PageHeader title="What happened?" />
        {restored && (
          <p className="mb-1 rounded-lg bg-warning-50 px-3 py-2 text-xs text-warning-700">
            You have an unfinished report. Choosing a type below starts a new one.
          </p>
        )}
        {KINDS.map(({ kind, label, hint, Icon, tone }) => (
          <button
            key={kind}
            type="button"
            onClick={() => {
              set('kind', kind)
              setForm((f) => ({
                ...f,
                kind,
                // Local, not UTC: this plant is at UTC-4, so after eight in
                // the evening the ISO date is already tomorrow.
                date: f.date || todayForInput(),
                time: f.time || nowForInput(),
              }))
            }}
            className="flex min-h-[64px] items-center gap-3 rounded-xl border border-neutral-200 bg-white p-3.5 text-left"
          >
            <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-lg ${tone}`}>
              <Icon className="h-5 w-5" aria-hidden />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-neutral-900">{label}</span>
              <span className="block text-xs text-neutral-500">{hint}</span>
            </span>
          </button>
        ))}
      </div>
    )
  }

  // ── Step 2: the details ──────────────────────────────────────────────────
  // ★ HSE, round 4: "refuse future dates on all 3 incident types. warn the user
  // that they selected a future date by mistake." Said as soon as it is typed —
  // the server refuses it too, but being told at the field is the difference
  // between a corrected typo and a rejected form.
  const inTheFuture = Boolean(form.date) && (() => {
    const [y, m, d] = form.date.split('-').map(Number)
    // ★ The same default the submit uses. It read '23:59' here and '00:00'
    // there, so clearing the time on today's incident made this judge it as
    // future and grey the button out until midnight — while the server would
    // have accepted it. Found in review by uniops-9a.
    const [hh, mm] = (form.time || '00:00').split(':').map(Number)
    // Built from parts rather than parsed: `new Date('2026-09-29')` is midnight
    // UTC, which is the previous evening here and has caused this exact class of
    // off-by-a-day across the whole of UniOps.
    return new Date(y, (m ?? 1) - 1, d ?? 1, hh ?? 0, mm ?? 0).getTime() > Date.now()
  })()

  const canSubmit = form.title.trim() && form.date && !inTheFuture && form.incidentType &&
    // A type that carries no severity has to have one supplied, or the injury
    // rates lose a count that nobody will notice is missing.
    (!asksInjuryClass || form.injuryClass) &&
    (form.kind !== 'near_miss' || form.talkedToOperator !== 'no' || form.whyNot.trim())

  return (
    <form
      className="flex flex-col gap-1 p-4 pb-24"
      onSubmit={(e) => {
        e.preventDefault()
        if (!submit.isPending && canSubmit) submit.mutate()
      }}
    >
      <button
        type="button"
        onClick={() => set('kind', null)}
        className="mb-1 flex min-h-[44px] items-center gap-1.5 self-start text-sm font-medium text-neutral-600"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden />
        Change type
      </button>

      {draftWarning && (
        <p className="mb-2 rounded-lg bg-warning-50 px-3 py-2 text-xs text-warning-700">
          {draftWarning}
        </p>
      )}

      <Field label="What happened?" required>
        <input
          className={inputClass}
          value={form.title}
          onChange={(e) => set('title', e.target.value)}
          placeholder="A short description"
        />
      </Field>

      <div className="grid grid-cols-2 gap-2">
        <Field label="Date" required>
          <input type="date" className={inputClass} value={form.date}
                 max={todayForInput()}
                 onChange={(e) => set('date', e.target.value)} />
        </Field>
        <Field label="Time">
          <input type="time" className={inputClass} value={form.time}
                 onChange={(e) => set('time', e.target.value)} />
        </Field>
      </div>
      {inTheFuture && (
        <p className="rounded-lg bg-warning-50 px-3 py-2 text-sm text-warning-800">
          That is in the future. An incident is reported after it happens, and
          the Ministry and WSIB clocks both run from when it happened — check
          the date and time.
        </p>
      )}

      <Field label="What kind of incident was this?" required>
        {/* ★ Round 5: "when selecting unconscious, automatically default to
            medical aid … first aid injury should default the HB picker to
            first aid. lost time kind of incident is a HB lost time", and from
            the answers, Report Only implies nobody was hurt.

            The default is applied when the type is chosen and then left alone
            — a default is a starting point, not a rule. Somebody who changes
            it has their answer kept, which is the behaviour the stored value
            already had: the type is only the fallback. */}
        <select className={inputClass} value={form.incidentType}
                onChange={(e) => {
                  const code = e.target.value
                  const picked = (incidentTypes ?? []).find((t) => t.code === code)
                  setForm((f) => ({
                    ...f,
                    incidentType: code,
                    injuryClass: picked?.default_injury_class ?? '',
                  }))
                }}>
          <option value="">Select the type</option>
          {(incidentTypes ?? []).map((t) => (
            <option key={t.code} value={t.code}>{t.label}</option>
          ))}
        </select>
      </Field>

      {/* Fatality and Unconscious tell us the Ministry must be told; they say
          nothing about lost time, and a death is nearly always — but not
          always — a lost-time injury. Asked rather than assumed. */}
      {/* ★ Asked on every type except Report Only and Fatality. Round 4: "add
          'how bad was the person hurt' to all 'what kind of incident was this'
          to allow for dashboard reporting, except report only/no injury and
          fatality selections."

          "Nobody was hurt" is an option rather than leaving the field blank. A
          near miss or a damaged machine usually did hurt nobody, and blank
          cannot tell that apart from not answered — on the field that drives
          LTIFR that difference matters. A fatality is not asked and is recorded
          as lost time regardless, because it is: leaving it empty would drop it
          out of the one figure where it most needs to appear. */}
      {asksInjuryClass && (
        <Field label="How badly was the person hurt?" required
               help="On the dashboard this is what the injury figures count.">
          <select className={inputClass} value={form.injuryClass}
                  onChange={(e) => set('injuryClass', e.target.value)}>
            <option value="">Select</option>
            <option value="none">Nobody was hurt</option>
            <option value="first_aid">First aid</option>
            <option value="medical_aid">Medical aid</option>
            <option value="lost_time">Lost time</option>
          </select>
        </Field>
      )}
      {/* ★ HSE's own test for lost time, where the choice is made. Nothing in
          the system classifies an injury — the reporter picks it — so whether
          it is picked right depends on whether they know the test, and the
          answer drives LTIFR and the WSIB clock. The words are HSE's, from
          Settings, because a regulatory definition invented here would be
          wrong and would be relied on anyway. Nothing is shown until they
          write it. */}
      {asksInjuryClass && lostTimeGuidance && (
        <p className="-mt-1 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
          <span className="font-medium text-neutral-800">Lost time means: </span>
          {lostTimeGuidance}
        </p>
      )}
      {/* The shared picker rather than a flat list of leaves. HSE asked for
          the general area first and the line last and optional, because
          "employees may not know the building numbers" — and because a
          leaf-only list would force a reporter who does not know which line it
          happened on to guess one. */}
      <Field label="Where did this happen?">
        <LocationSelect
          value={form.locationId || null}
          onChange={(id) => set('locationId', id ?? '')}
        />
      </Field>

      {form.kind === 'medical' && (
        <Field
        label="Who was hurt?"
        help={form.injured.departmentName
          ? `Department: ${form.injured.departmentName} — filled in from their record.`
          : 'Search for an employee, or type the name of a contractor or visitor.'}
      >
          <PersonPicker value={form.injured} onChange={(v) => set('injured', v)} />
        </Field>
      )}

      {/* ★ Round 7: "make the following fields viewable only when a name is
          entered into the who was hurt, what part of the body, what kind of
          injury, who gave treatment, and what treatment was given." */}
      {form.kind === 'medical' && namedInjured && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="What part of the body?"
                 help="Grouped the way WSIB groups them, so Form 7 can be filled from this.">
            <VocabularySelect vocabulary="body_part" value={form.bodyPart?.id ?? null}
                              onChange={(v) => set('bodyPart', v)} />
          </Field>
          <Field label="What kind of injury?">
            <VocabularySelect vocabulary="nature_of_injury"
                              value={form.natureOfInjury?.id ?? null}
                              onChange={(v) => set('natureOfInjury', v)} />
          </Field>
        </div>
      )}

      {form.kind === 'medical' && namedInjured && (
        <>
          {/* The first aid register, on the form rather than beside it.
              Regulation 1101 wants a record of the treatment; it does not
              want it in a second place. */}
          <Field label="What treatment was given?"
                 help="Written at the time by whoever gave it — this is the Regulation 1101 record.">
            <textarea className={`${inputClass} min-h-[70px] py-2`} value={form.treatment}
                      onChange={(e) => set('treatment', e.target.value)}
                      placeholder="Cleaned and dressed a shallow cut to the left index finger." />
          </Field>

          <label className="flex items-center gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={form.sentOffsite}
                   onChange={(e) => set('sentOffsite', e.target.checked)} />
            They were sent off site for treatment
          </label>

          <Field label="Anything owed afterwards?" help="A dressing to check, a follow-up call.">
            <input className={inputClass} value={form.followUp}
                   onChange={(e) => set('followUp', e.target.value)} />
          </Field>

          {/* ★ Who gave the treatment. Regulation 1101 asks for it by name, and
              the register printed nothing in that column for everything
              reported on this form — the role existed on the record and nothing
              ever filled it in. A written-in name is accepted, because the
              person who treated a cut may be a visiting nurse. */}
          <Field label="Who gave the treatment?"
                 help="The first aider who treated them. Regulation 1101 asks for the name.">
            <PersonPicker value={form.firstAider} onChange={(v) => set('firstAider', v)} />
          </Field>
        </>
      )}

      {showInjuryDetail && (
        <>
          {/* Section B, on the report rather than only on the investigation.
              It was reachable before, but a screen later: by the time anyone
              opened it the pulse and the pain score were being written from
              memory. The timeline stays there — it is built as treatment goes
              on, not in the minute the form is filled in. */}
          <p className="mt-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
            <span className="font-semibold">Private.</span> The WSIB detail below is
            visible only to people who may read medical information, and can be
            added to later from the investigation.
          </p>
          <InjuryDetailFields
            value={form.injury}
            onChange={(v) => set('injury', v)}
            showTimeline={false}
          />
        </>
      )}

      {form.kind === 'near_miss' && (
        <>
          <Field label="Did you talk to the operator at the time?" required>
            <div className="flex gap-2">
              {(['yes', 'no', 'na'] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => set('talkedToOperator', v)}
                  className={`min-h-[44px] flex-1 rounded-lg border text-sm font-semibold ${
                    form.talkedToOperator === v
                      ? v === 'no'
                        ? 'border-danger-600 bg-danger-600 text-white'
                        : 'border-primary-600 bg-primary-600 text-white'
                      : 'border-neutral-300 bg-white text-neutral-600'
                  }`}
                >
                  {v === 'na' ? 'N/A' : v === 'yes' ? 'Yes' : 'No'}
                </button>
              ))}
            </div>
          </Field>
          {/* Shown only after "No", and it says why it appeared. */}
          {form.talkedToOperator === 'no' && (
            <Field label="Why not?" required help="Shown because you answered No above.">
              <textarea className={inputClass} rows={2} value={form.whyNot}
                        onChange={(e) => set('whyNot', e.target.value)} />
            </Field>
          )}
        </>
      )}

      <Field label="What else should we know?">
        <textarea className={inputClass} rows={3} value={form.description}
                  onChange={(e) => set('description', e.target.value)} />
      </Field>

      <Field label="Photographs">
        <PhotoCapture photos={photos} onChange={setPhotos} />
      </Field>

      <Field label="Sign">
        <SignaturePad
          value={form.signature}
          onChange={(v) => set('signature', v)}
          savedSignature={profile.data?.signature_image ?? null}
        />
      </Field>

      <label className="mt-2 flex min-h-[44px] items-center gap-2.5 text-sm text-neutral-700">
        <input type="checkbox" className="h-4 w-4" checked={form.anonymous}
               onChange={(e) => set('anonymous', e.target.checked)} />
        Report anonymously
      </label>
      {form.anonymous && (
        <p className="-mt-1 text-xs text-neutral-500">
          Your name will not appear on the report. HSE can still see that a report was
          filed, so they can follow up on the area rather than on you.
        </p>
      )}

      {submit.isError && (
        <p className="mt-2 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {(submit.error as ApiError).message}
        </p>
      )}

      <div className="fixed inset-x-0 bottom-16 z-10 flex gap-2 border-t border-neutral-200 bg-white p-3 md:static md:mt-4 md:border-0 md:p-0">
        <button
          type="button"
          onClick={() => { discardDraft(DRAFT_KIND, DRAFT_ID); setForm(emptyForm()); setPhotos([]) }}
          className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 bg-white px-4 text-sm font-semibold text-neutral-600"
        >
          Discard
        </button>
        <button
          type="submit"
          disabled={submit.isPending || !canSubmit}
          className="min-h-[44px] flex-[2] rounded-lg bg-primary-600 px-4 text-sm font-semibold text-white disabled:opacity-50"
        >
          {submit.isPending ? 'Submitting…' : 'Submit'}
        </button>
      </div>
    </form>
  )
}

const inputClass =
  'w-full min-h-[44px] rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-900'

function Field({
  label, required, help, children,
}: {
  label: string
  required?: boolean
  help?: string
  children: React.ReactNode
}) {
  return (
    <label className="block pt-2">
      <span className="mb-1.5 block text-xs font-semibold text-neutral-600">
        {label}
        {required && <span className="ml-0.5 text-danger-600">*</span>}
      </span>
      {children}
      {help && <span className="mt-1 block text-xs text-neutral-400">{help}</span>}
    </label>
  )
}
