/**
 * Assessing a risk against HSE's matrix.
 *
 * The rating is not chosen — it is read off the grid once likelihood and
 * severity are picked, and the server computes it again on save. The matrix is
 * the company's document; an assessment that disagrees with it would be worse
 * than none.
 *
 * Severity asks which consequence type it is being judged on, because HSE's
 * matrix describes every band five different ways — people, downtime,
 * reputation, environmental, asset damage. Without that, "severity 3" is not
 * interpretable afterwards.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'

interface Likelihood {
  code: string; label: string; qualitative: string | null
  frequency: string | null; ratings: Record<string, string>
}
interface Severity {
  level: number; people: string | null; downtime: string | null
  reputation: string | null; environmental: string | null; asset_damage: string | null
}
interface Matrix {
  likelihood: Likelihood[]; severity: Severity[]; consequence_types: string[]
}

const CONSEQUENCE_LABELS: Record<string, string> = {
  people: 'People', downtime: 'Unplanned downtime', reputation: 'Reputation',
  environmental: 'Environmental', asset_damage: 'Asset damage',
}

const RATING_STYLE: Record<string, string> = {
  acceptable: 'bg-primary-50 text-primary-700 border-primary-200',
  moderate: 'bg-warning-50 text-warning-700 border-warning-200',
  serious: 'bg-orange-50 text-orange-700 border-orange-200',
  critical: 'bg-danger-50 text-danger-700 border-danger-200',
}

const inputClass =
  'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
  'focus:border-primary-400 focus:outline-none'

export function RiskAssessmentDialog({
  sourceType, sourceId, onClose,
}: {
  sourceType: string
  sourceId: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [hazard, setHazard] = useState('')
  const [consequence, setConsequence] = useState('people')
  const [likelihood, setLikelihood] = useState<string | null>(null)
  const [severity, setSeverity] = useState<number | null>(null)
  const [controls, setControls] = useState('')
  const [error, setError] = useState<string | null>(null)

  const matrix = useQuery({
    queryKey: ['risk-matrix'],
    queryFn: () => api.get<Matrix>('/api/v1/risk/matrix'),
  })

  // Read off the grid as soon as both axes are chosen, so the consequence of a
  // choice is visible while making it.
  const rating = (likelihood && severity !== null)
    ? matrix.data?.likelihood.find((l) => l.code === likelihood)?.ratings[String(severity)]
    : undefined

  const save = useMutation({
    mutationFn: () => api.post('/api/v1/risk/assessments', {
      source_type: sourceType, source_id: sourceId,
      hazard_label: hazard || null,
      consequence_type: consequence,
      likelihood_code: likelihood, severity_level: severity,
      controls: controls || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['risk-assessments', sourceId] })
      onClose()
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  const severityText = (s: Severity) =>
    (s[consequence as keyof Severity] as string | null) ?? '—'

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
      <div className="max-h-[92vh] w-full overflow-y-auto rounded-t-2xl bg-white p-4 sm:max-w-lg sm:rounded-2xl">
        <div className="mb-3 flex items-start justify-between">
          <h2 className="text-base font-bold text-neutral-900">Risk assessment</h2>
          <button type="button" onClick={onClose}
                  className="flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400">
            <X className="h-5 w-5" aria-hidden /><span className="sr-only">Close</span>
          </button>
        </div>

        <label className="mb-1 block text-sm font-medium text-neutral-800">Hazard</label>
        <input className={inputClass} value={hazard} onChange={(e) => setHazard(e.target.value)}
               placeholder="What could cause harm" />

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">
          Judged on
        </label>
        <p className="mb-1.5 text-xs text-neutral-500">
          The matrix describes each severity band five ways. Recording which one was used is
          what makes the number mean something later.
        </p>
        <select className={inputClass} value={consequence}
                onChange={(e) => setConsequence(e.target.value)}>
          {matrix.data?.consequence_types.map((c) => (
            <option key={c} value={c}>{CONSEQUENCE_LABELS[c] ?? c}</option>
          ))}
        </select>

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">
          How likely
        </label>
        <div className="flex flex-col gap-1">
          {matrix.data?.likelihood.map((l) => (
            <button key={l.code} type="button" onClick={() => setLikelihood(l.code)}
                    aria-pressed={likelihood === l.code}
                    className={`flex min-h-[40px] items-center gap-2 rounded-lg border px-2.5 text-left text-sm ${
                      likelihood === l.code
                        ? 'border-primary-300 bg-primary-50 text-primary-800'
                        : 'border-neutral-200 bg-white text-neutral-700'}`}>
              <span className="w-5 font-mono text-xs">{l.code}</span>
              <span className="min-w-0 flex-1">{l.label}</span>
              <span className="text-xs text-neutral-500">{l.qualitative}</span>
            </button>
          ))}
        </div>

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">
          How bad
        </label>
        <div className="flex flex-col gap-1">
          {[...(matrix.data?.severity ?? [])].reverse().map((s) => (
            <button key={s.level} type="button" onClick={() => setSeverity(s.level)}
                    aria-pressed={severity === s.level}
                    className={`flex min-h-[40px] items-center gap-2 rounded-lg border px-2.5 text-left text-sm ${
                      severity === s.level
                        ? 'border-primary-300 bg-primary-50 text-primary-800'
                        : 'border-neutral-200 bg-white text-neutral-700'}`}>
              <span className="w-5 font-mono text-xs">{s.level}</span>
              <span className="min-w-0 flex-1">{severityText(s)}</span>
            </button>
          ))}
        </div>

        {rating && (
          <p className={`mt-3 rounded-lg border px-3 py-2 text-sm font-semibold capitalize ${
            RATING_STYLE[rating] ?? 'border-neutral-200 bg-neutral-50'}`}>
            {rating}
          </p>
        )}

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">
          Controls
        </label>
        <textarea rows={3} className={`${inputClass} py-2`} value={controls}
                  onChange={(e) => setControls(e.target.value)} />

        {error && <p className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>}

        <div className="mt-4 flex gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 text-sm font-medium text-neutral-700">
            Cancel
          </button>
          <button type="button"
                  disabled={!likelihood || severity === null || save.isPending}
                  onClick={() => { if (likelihood && severity !== null && !save.isPending) { setError(null); save.mutate() } }}
                  className="min-h-[44px] flex-[1.4] rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50">
            {save.isPending ? 'Saving…' : 'Save assessment'}
          </button>
        </div>
      </div>
    </div>
  )
}
