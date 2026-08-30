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
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, Eye, Wrench } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { discardDraft, loadDraft, saveDraft } from '@/lib/draftStore'
import type { CompressedImage } from '@/lib/imageCompress'
import { PhotoCapture } from '@/components/PhotoCapture'
import { SignaturePad } from '@/components/SignaturePad'

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

interface LocationNode {
  id: string
  name: string
  path: string
  depth: number
  children: LocationNode[]
}

interface FormState {
  kind: Kind | null
  title: string
  date: string
  time: string
  locationId: string
  description: string
  injuredName: string
  talkedToOperator: '' | 'yes' | 'no' | 'na'
  whyNot: string
  anonymous: boolean
  signature: string | null
}

const EMPTY: FormState = {
  kind: null, title: '', date: '', time: '', locationId: '', description: '',
  injuredName: '', talkedToOperator: '', whyNot: '', anonymous: false, signature: null,
}

const DRAFT_KIND = 'incident'
const DRAFT_ID = 'current'

function flatten(nodes: LocationNode[], out: LocationNode[] = []): LocationNode[] {
  for (const n of nodes) {
    out.push(n)
    flatten(n.children ?? [], out)
  }
  return out
}

export default function IncidentReportPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [form, setForm] = useState<FormState>(EMPTY)
  const [photos, setPhotos] = useState<CompressedImage[]>([])
  const [restored, setRestored] = useState(false)
  const [draftWarning, setDraftWarning] = useState<string | null>(null)

  // Restore whatever was being typed when the page was last left.
  useEffect(() => {
    const draft = loadDraft<{ form: FormState; photos: CompressedImage[] }>(DRAFT_KIND, DRAFT_ID)
    if (draft) {
      setForm(draft.data.form)
      setPhotos(draft.data.photos ?? [])
      setRestored(true)
    }
  }, [])

  // Save on every change once something has actually been entered.
  useEffect(() => {
    if (!form.kind) return
    const ok = saveDraft(DRAFT_KIND, DRAFT_ID, { form, photos })
    setDraftWarning(ok ? null
      : 'This draft is too large to keep on the phone — submit it while you have signal.')
  }, [form, photos])

  const { data: locations } = useQuery({
    queryKey: ['locations'],
    queryFn: () => api.get<LocationNode[]>('/api/v1/settings/locations'),
    staleTime: 10 * 60_000,
  })
  const flatLocations = useMemo(() => flatten(locations ?? []), [locations])

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
      }
      if (form.kind === 'medical' && form.injuredName.trim()) {
        body.persons = [{ role: 'injured', person_name: form.injuredName.trim() }]
      }
      if (form.kind === 'near_miss') {
        body.extra = {
          talked_to_operator: form.talkedToOperator || null,
          why_not: form.talkedToOperator === 'no' ? form.whyNot : null,
        }
      }
      const created = await api.post<{ id: string }>('/api/v1/incidents', body)
      await api.post(`/api/v1/incidents/${created.id}/submit`)
      return created
    },
    onSuccess: (created) => {
      discardDraft(DRAFT_KIND, DRAFT_ID)
      queryClient.invalidateQueries({ queryKey: ['incidents'] })
      navigate(`/incidents/${created.id}`)
    },
  })

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  // ── Step 1: what happened ────────────────────────────────────────────────
  if (!form.kind) {
    return (
      <div className="flex flex-col gap-2 p-4">
        <h1 className="pb-1 text-lg font-bold tracking-tight text-neutral-900">
          What happened?
        </h1>
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
              const now = new Date()
              set('kind', kind)
              setForm((f) => ({
                ...f,
                kind,
                date: f.date || now.toISOString().slice(0, 10),
                time: f.time || now.toTimeString().slice(0, 5),
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
  const canSubmit = form.title.trim() && form.date &&
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
                 onChange={(e) => set('date', e.target.value)} />
        </Field>
        <Field label="Time">
          <input type="time" className={inputClass} value={form.time}
                 onChange={(e) => set('time', e.target.value)} />
        </Field>
      </div>

      <Field label="Where did this happen?">
        <select className={inputClass} value={form.locationId}
                onChange={(e) => set('locationId', e.target.value)}>
          <option value="">Select an area</option>
          {flatLocations.map((l) => (
            <option key={l.id} value={l.id}>
              {' '.repeat(l.depth * 2)}{l.name}
            </option>
          ))}
        </select>
      </Field>

      {form.kind === 'medical' && (
        <Field label="Who was hurt?">
          <input className={inputClass} value={form.injuredName}
                 onChange={(e) => set('injuredName', e.target.value)}
                 placeholder="Name — a contractor or visitor can be named here too" />
        </Field>
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
        <SignaturePad value={form.signature} onChange={(v) => set('signature', v)} />
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
          onClick={() => { discardDraft(DRAFT_KIND, DRAFT_ID); setForm(EMPTY); setPhotos([]) }}
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
