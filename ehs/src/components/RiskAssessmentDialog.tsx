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
import { VocabularyMultiSelect, VocabularySelect } from '@/components/VocabularyPicker'

interface Likelihood {
  code: string; label: string; qualitative: string | null
  frequency: string | null; ratings: Record<string, string>
}
interface Severity {
  level: number; people: string | null; downtime: string | null
  reputation: string | null; environmental: string | null; asset_damage: string | null
}
interface Consequence { code: string; label: string; severity: Record<string, string> }
interface Matrix {
  likelihood: Likelihood[]; severity: Severity[]; consequence_types: string[]
  consequences?: Consequence[]
}

export const RATING_STYLE: Record<string, string> = {
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
  // ★ Round 8, question 5 — the form as HSE confirmed it, top to bottom:
  // Existing Hazard from the hazard list; Judged on (HSE's list, with
  // Equipment); how likely and how bad as things stand; the hierarchy of
  // controls and the hazard controls; the hazard after controls are in; how
  // likely and how bad then — the final assessment, which is what decides
  // whether the risk is acceptable.
  const queryClient = useQueryClient()
  const [hazardId, setHazardId] = useState<string | null>(null)
  const [consequence, setConsequence] = useState<string | null>(null)
  const [likelihood, setLikelihood] = useState<string | null>(null)
  const [severity, setSeverity] = useState<number | null>(null)
  const [hierarchy, setHierarchy] = useState<string[]>([])
  const [hazardControls, setHazardControls] = useState<string[]>([])
  const [remaining, setRemaining] = useState('')
  const [residualLikelihood, setResidualLikelihood] = useState<string | null>(null)
  const [residualSeverity, setResidualSeverity] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const matrix = useQuery({
    queryKey: ['risk-matrix'],
    queryFn: () => api.get<Matrix>('/api/v1/risk/matrix'),
  })
  // A consequence with no wording for its levels cannot be scored the same way
  // twice, so it is not offered until HSE has written it under Settings → Lists.
  const consequences = (matrix.data?.consequences ?? []).filter((c) => Object.keys(c.severity).length > 0)
  const judged = consequences.find((c) => c.code === consequence) ?? null
  const ratingOf = (l: string | null, sv: number | null) => (l && sv !== null)
    ? matrix.data?.likelihood.find((x) => x.code === l)?.ratings[String(sv)] : undefined
  const before = ratingOf(likelihood, severity)
  const after = ratingOf(residualLikelihood, residualSeverity)
  const complete = Boolean(hazardId && judged && likelihood && severity !== null
    && residualLikelihood && residualSeverity !== null)

  const save = useMutation({
    mutationFn: () => api.post('/api/v1/risk/assessments', {
      source_type: sourceType, source_id: sourceId,
      hazard_item_id: hazardId,
      consequence_type: consequence,
      likelihood_code: likelihood, severity_level: severity,
      hierarchy_control_ids: hierarchy,
      hazard_control_ids: hazardControls,
      controls: remaining.trim() || null,
      residual_likelihood_code: residualLikelihood,
      residual_severity_level: residualSeverity,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['risk-assessments', sourceId] })
      onClose()
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  const label = 'mb-1 mt-4 block text-sm font-medium text-neutral-800'
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
        {matrix.isError && <p className="mb-2 text-sm text-danger-700">The risk matrix could not be loaded.</p>}

        <span className={label.replace('mt-4', 'mt-0')}>Existing Hazard</span>
        <VocabularySelect vocabulary="hazard" value={hazardId}
                          onChange={(item) => setHazardId(item?.id ?? null)}
                          placeholder="Pick from the hazard list" />

        <span className={label}>Judged on</span>
        <select className={inputClass} aria-label="Judged on" value={consequence ?? ''}
                onChange={(e) => { setConsequence(e.target.value || null); setSeverity(null); setResidualSeverity(null) }}>
          <option value="">Choose…</option>
          {consequences.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
        </select>

        <Scoring title="Before controls" judged={judged} matrix={matrix.data}
                 likelihood={likelihood} severity={severity}
                 onLikelihood={setLikelihood} onSeverity={setSeverity} rating={before}
                 ratingLabel="Risk level" />

        <span className={label}>Hierarchy of controls</span>
        <VocabularyMultiSelect vocabulary="hierarchy_of_control" value={hierarchy}
                               onChange={(ids) => setHierarchy(ids)} placeholder="Pick any…" />

        <span className={label}>Hazard controls</span>
        <VocabularyMultiSelect vocabulary="hazard_control" value={hazardControls}
                               onChange={(ids) => setHazardControls(ids)} placeholder="Search the hazard controls…" />

        <label className={label} htmlFor="risk-remaining">Hazard after controls are implemented</label>
        <textarea id="risk-remaining" rows={3} className={`${inputClass} py-2`} value={remaining}
                  onChange={(e) => setRemaining(e.target.value)}
                  placeholder="What remains once the controls are in place" />

        <Scoring title="After controls" judged={judged} matrix={matrix.data}
                 likelihood={residualLikelihood} severity={residualSeverity}
                 onLikelihood={setResidualLikelihood} onSeverity={setResidualSeverity} rating={after}
                 ratingLabel="Final assessment" final />

        {error && <p className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>}
        {!complete && (
          <p className="mt-3 text-xs text-neutral-500">
            The hazard, what it is judged on, and how likely and how bad both before and after
            controls are needed to save.
          </p>
        )}

        <div className="mt-4 flex gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 text-sm font-medium text-neutral-700">
            Cancel
          </button>
          <button type="button"
                  disabled={!complete || save.isPending}
                  onClick={() => { if (complete && !save.isPending) { setError(null); save.mutate() } }}
                  className="min-h-[44px] flex-[1.4] rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50">
            {save.isPending ? 'Saving…' : 'Save assessment'}
          </button>
        </div>
      </div>
    </div>
  )
}

/** How likely and how bad, and the rating they read off the matrix. */
function Scoring({ title, judged, matrix, likelihood, severity, onLikelihood, onSeverity,
                   rating, ratingLabel, final = false }: {
  title: string; judged: Consequence | null; matrix: Matrix | undefined
  likelihood: string | null; severity: number | null
  onLikelihood: (code: string) => void; onSeverity: (level: number) => void
  rating: string | undefined; ratingLabel: string; final?: boolean
}) {
  const levels = Object.keys(judged?.severity ?? {}).map(Number).sort((a, b) => b - a)
  return (
    <div className="mt-5 rounded-xl border border-neutral-200 p-3">
      <p className="text-xs font-semibold uppercase tracking-wider text-neutral-500">{title}</p>
      <span className="mb-1 mt-2 block text-sm font-medium text-neutral-800">How likely</span>
      <div className="flex flex-col gap-1">
        {matrix?.likelihood.map((l) => (
          <button key={l.code} type="button" onClick={() => onLikelihood(l.code)}
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
      <span className="mb-1 mt-3 block text-sm font-medium text-neutral-800">How bad</span>
      {!judged ? (
        <p className="text-xs text-neutral-500">Choose what it is judged on first.</p>
      ) : (
        <div className="flex flex-col gap-1">
          {levels.map((lv) => (
            <button key={lv} type="button" onClick={() => onSeverity(lv)}
                    aria-pressed={severity === lv}
                    className={`flex min-h-[40px] items-center gap-2 rounded-lg border px-2.5 text-left text-sm ${
                      severity === lv
                        ? 'border-primary-300 bg-primary-50 text-primary-800'
                        : 'border-neutral-200 bg-white text-neutral-700'}`}>
              <span className="w-5 font-mono text-xs">{lv}</span>
              <span className="min-w-0 flex-1">{judged.severity[String(lv)]}</span>
            </button>
          ))}
        </div>
      )}
      {rating && (
        <p className={`mt-3 rounded-lg border px-3 py-2 text-sm font-semibold ${
          RATING_STYLE[rating] ?? 'border-neutral-200 bg-neutral-50'}`}>
          {ratingLabel}: <span className="capitalize">{rating}</span>
          {final && (rating === 'acceptable' ? ' — the remaining risk is acceptable'
                                             : ' — still too high: more controls are needed')}
        </p>
      )}
    </div>
  )
}
