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
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldAlert, Plus, X } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { LocationSelect } from '@/components/LocationSelect'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { VocabularySelect, type VocabItem } from '@/components/VocabularyPicker'
import {
  Card, Empty, ErrorNote, Field, PageHeader, inputClass, primaryButton, secondaryButton,
} from '@/components/ui'

export interface Hazard {
  id: string; hazard_no: string; title: string; description: string | null
  hazard_type_label: string | null
  location_id: string | null; location_path: string | null; department_name: string | null
  risk_assessment_id: string | null
  current_rating: string | null; residual_rating: string | null
  existing_controls: string | null; control_level: string | null
  owner_id: string | null; owner_name: string | null
  identified_on: string | null; review_due_on: string | null
  status: string; created_at: string
}
interface RatingCount { rating: string; count: number }
interface Likelihood { code: string; label: string; qualitative: string | null; frequency: string | null }
interface Severity {
  level: number; people: string | null; downtime: string | null
  reputation: string | null; environmental: string | null; asset_damage: string | null
}
interface Matrix { likelihood: Likelihood[]; severity: Severity[]; consequence_types: string[] }

/** The matrix's own order, not the alphabet's. */
const RATING_TONE: Record<string, string> = {
  critical: 'bg-danger-600 text-white',
  serious: 'bg-danger-50 text-danger-700',
  moderate: 'bg-warning-50 text-warning-700',
  acceptable: 'bg-success-50 text-success-700',
}

export const CONTROL_LEVELS = [
  { key: 'elimination', label: 'Elimination' },
  { key: 'substitution', label: 'Substitution' },
  { key: 'engineering', label: 'Engineering control' },
  { key: 'administrative', label: 'Administrative control' },
  { key: 'ppe', label: 'PPE' },
]

export function RatingBadge({ rating }: { rating: string | null }) {
  if (!rating) {
    return (
      <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-500">
        Not assessed
      </span>
    )
  }
  const tone = RATING_TONE[rating.toLowerCase()] ?? 'bg-neutral-100 text-neutral-600'
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold capitalize ${tone}`}>
      {rating}
    </span>
  )
}

export default function HazardsPage() {
  const { can } = usePermissions()
  const mayWrite = can('ehs.action.write')
  const [creating, setCreating] = useState(false)
  const [assessing, setAssessing] = useState<Hazard | null>(null)
  const [showEliminated, setShowEliminated] = useState(false)

  const query = showEliminated ? '' : '?status=open&status=controlled'
  const hazards = useQuery({
    queryKey: ['hazards', showEliminated],
    queryFn: () => api.get<Hazard[]>(`/api/v1/hazards${query}`),
  })
  const summary = useQuery({
    queryKey: ['hazard-summary'],
    queryFn: () => api.get<RatingCount[]>('/api/v1/hazards/summary'),
  })

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

      {creating && <NewHazardForm onClose={() => setCreating(false)} />}

      <label className="mb-3 flex items-center gap-2 text-xs text-neutral-600">
        <input type="checkbox" checked={showEliminated}
               onChange={(e) => setShowEliminated(e.target.checked)} />
        Include hazards that have been eliminated
      </label>

      {hazards.data?.length === 0 && !creating ? (
        <Empty>
          Nothing on the register. A hazard recorded here is a standing fact
          about the plant — it stays until it is designed out, and comes back
          round for review while it has not been.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {hazards.data?.map((h) => (
            <li key={h.id}>
              <Card>
                <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5">
                  <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-neutral-400" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">
                      {h.title}
                    </span>
                    <span className="mt-0.5 block text-xs text-neutral-500">
                      {h.hazard_no}
                      {h.location_path && ` · ${h.location_path}`}
                      {h.owner_name && ` · owned by ${h.owner_name}`}
                    </span>
                    {h.description && (
                      <span className="mt-1 block text-sm text-neutral-700">{h.description}</span>
                    )}
                    {h.existing_controls && (
                      <span className="mt-1 block text-xs text-neutral-600">
                        <span className="font-medium">Controls:</span> {h.existing_controls}
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
                      <button type="button" onClick={() => setAssessing(h)}
                              className="text-xs font-semibold text-primary-700 hover:underline">
                        {h.current_rating ? 'Reassess' : 'Assess the risk'}
                      </button>
                    )}
                  </span>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {assessing && (
        <AssessDialog hazard={assessing} onClose={() => setAssessing(null)} />
      )}
    </div>
  )
}

function NewHazardForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [type, setType] = useState<VocabItem | null>(null)
  const [locationId, setLocationId] = useState<string | null>(null)
  const [controls, setControls] = useState('')
  const [level, setLevel] = useState('')
  const [owner, setOwner] = useState<PersonValue>(EMPTY_PERSON)
  const [reviewOn, setReviewOn] = useState('')

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/hazards', {
      title, description: description || null,
      hazard_type_id: type?.id ?? null, hazard_type_label: type?.label ?? null,
      location_id: locationId,
      existing_controls: controls || null,
      control_level: level || null,
      owner_id: owner.userId,
      review_due_on: reviewOn || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['hazards'] })
      queryClient.invalidateQueries({ queryKey: ['hazard-summary'] })
      onClose()
    },
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">Record a hazard</h2>
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
        <Field label="Kind">
          <VocabularySelect vocabulary="hazard" value={type?.id ?? null} onChange={setType} />
        </Field>
        <Field label="Where">
          <LocationSelect value={locationId} onChange={setLocationId} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Description">
            <textarea className={`${inputClass} min-h-[70px] py-2`} value={description}
                      onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="Controls already in place">
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
          <PersonPicker value={owner} onChange={setOwner} allowExternal={false} />
        </Field>
        <Field label="Review due">
          <input type="date" className={inputClass} value={reviewOn}
                 onChange={(e) => setReviewOn(e.target.value)} />
        </Field>
      </div>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!title.trim() || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Recording…' : 'Record'}
        </button>
      </div>
    </Card>
  )
}

/**
 * Scoring a hazard against HSE's matrix.
 *
 * Both halves are asked in one place — the risk as it stands, and the risk
 * once the controls are in. Assessing without the second is the common way a
 * register ends up full of critical hazards that were in fact dealt with.
 */
function AssessDialog({ hazard, onClose }: { hazard: Hazard; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [consequence, setConsequence] = useState('people')
  const [likelihood, setLikelihood] = useState('')
  const [severity, setSeverity] = useState('')
  const [controls, setControls] = useState(hazard.existing_controls ?? '')
  const [residualLikelihood, setResidualLikelihood] = useState('')
  const [residualSeverity, setResidualSeverity] = useState('')

  const matrix = useQuery({
    queryKey: ['risk-matrix'],
    queryFn: () => api.get<Matrix>('/api/v1/risk/matrix'),
    staleTime: 30 * 60_000,
  })

  const assess = useMutation({
    mutationFn: () => api.post(`/api/v1/hazards/${hazard.id}/assess`, {
      consequence_type: consequence,
      likelihood_code: likelihood,
      severity_level: Number(severity),
      controls: controls || null,
      residual_likelihood_code: residualLikelihood || null,
      residual_severity_level: residualSeverity === '' ? null : Number(residualSeverity),
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['hazards'] })
      queryClient.invalidateQueries({ queryKey: ['hazard-summary'] })
      onClose()
    },
  })

  const severityText = (s: Severity) =>
    (s[consequence as keyof Severity] as string | null) ?? '—'

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center overflow-y-auto bg-black/40 p-4 sm:items-center">
      <div role="dialog" aria-modal="true" aria-label="Assess the risk"
           className="w-full max-w-lg rounded-xl bg-white p-4 shadow-xl">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-neutral-900">{hazard.title}</h2>
            <p className="text-xs text-neutral-500">{hazard.hazard_no}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close"
                  className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
            <X className="h-4 w-4" />
          </button>
        </div>

        <Field label="Judged on"
               hint="The matrix scores severity differently for each of these, so it has to be said which">
          <select className={inputClass} value={consequence}
                  onChange={(e) => setConsequence(e.target.value)}>
            {matrix.data?.consequence_types.map((c) => (
              <option key={c} value={c}>{c.replace('_', ' ')}</option>
            ))}
          </select>
        </Field>

        <p className="mb-1 mt-3 text-xs font-bold uppercase tracking-wider text-neutral-400">
          As it stands
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="How likely">
            <select className={inputClass} value={likelihood}
                    onChange={(e) => setLikelihood(e.target.value)}>
              <option value="">Select…</option>
              {matrix.data?.likelihood.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.code} — {l.label}{l.frequency ? ` (${l.frequency})` : ''}
                </option>
              ))}
            </select>
          </Field>
          <Field label="How bad">
            <select className={inputClass} value={severity}
                    onChange={(e) => setSeverity(e.target.value)}>
              <option value="">Select…</option>
              {matrix.data?.severity.map((s) => (
                <option key={s.level} value={s.level}>{s.level} — {severityText(s)}</option>
              ))}
            </select>
          </Field>
        </div>

        <div className="mt-3">
          <Field label="Controls">
            <textarea className={`${inputClass} min-h-[60px] py-2`} value={controls}
                      onChange={(e) => setControls(e.target.value)} />
          </Field>
        </div>

        <p className="mb-1 mt-3 text-xs font-bold uppercase tracking-wider text-neutral-400">
          Once those controls are in
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="How likely then">
            <select className={inputClass} value={residualLikelihood}
                    onChange={(e) => setResidualLikelihood(e.target.value)}>
              <option value="">Not assessed</option>
              {matrix.data?.likelihood.map((l) => (
                <option key={l.code} value={l.code}>{l.code} — {l.label}</option>
              ))}
            </select>
          </Field>
          <Field label="How bad then">
            <select className={inputClass} value={residualSeverity}
                    onChange={(e) => setResidualSeverity(e.target.value)}>
              <option value="">Not assessed</option>
              {matrix.data?.severity.map((s) => (
                <option key={s.level} value={s.level}>{s.level} — {severityText(s)}</option>
              ))}
            </select>
          </Field>
        </div>

        <ErrorNote error={assess.error} />

        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
          <button type="button" className={primaryButton}
                  disabled={!likelihood || severity === '' || assess.isPending}
                  onClick={() => { if (!assess.isPending) assess.mutate() }}>
            {assess.isPending ? 'Scoring…' : 'Score it'}
          </button>
        </div>
        <p className="mt-2 text-right text-xs text-neutral-500">
          The rating comes from the matrix, not from this form.
        </p>
      </div>
    </div>
  )
}
