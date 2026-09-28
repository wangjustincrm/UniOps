/**
 * A rating, and how a record came to have it.
 *
 * ★ This was on the hazard register and nowhere else, which is the fault Kirk
 * reported as "saving the risk assessment does not update the form with the
 * results entered". An incident has an <span>Assess the risk</span> button; the
 * assessment saved correctly every time and the incident then showed nothing at
 * all — no rating, no band, no sign it had happened. Somebody scoring an
 * incident had no way to tell whether their work had been recorded, and the
 * specification asks for a completed matrix to be kept with the record as part
 * of its history. Both halves are this component, so anything that can be
 * assessed shows its assessments in the same shape.
 *
 * The words go beside the numbers. "2" says nothing on its own — the second
 * acceptance round asked it outright, "is 2 bad or good?" — and the matrix
 * scores severity differently depending on which consequence it was judged
 * against, so the number is not interpretable without saying which.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { LoadFailedNote } from '@/components/ui'

/** The matrix's own order, not the alphabet's. */
const RATING_TONE: Record<string, string> = {
  critical: 'bg-danger-600 text-white',
  serious: 'bg-danger-50 text-danger-700',
  moderate: 'bg-warning-50 text-warning-700',
  acceptable: 'bg-success-50 text-success-700',
}

export interface Assessment {
  id: string; consequence_type: string
  likelihood_code: string; likelihood_label: string | null
  severity_level: number; severity_label: string | null
  rating: string; controls: string | null
  hazard_label: string | null
  residual_likelihood_code: string | null; residual_severity_level: number | null
  residual_rating: string | null
  assessed_by_name: string | null; assessed_at: string
}

export function RatingBadge({ rating }: { rating: string | null }) {
  if (!rating) {
    return (
      <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600">
        Not assessed
      </span>
    )
  }
  const tone = RATING_TONE[rating.toLowerCase()] ?? 'bg-neutral-100 text-neutral-600'
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold capitalize ${tone}`}>
      {rating}
    </span>
  )
}

/** The query key an assessment dialog invalidates after saving. */
export const scoringKey = (sourceId: string) => ['risk-assessments', sourceId]

export function useScorings(sourceType: string, sourceId: string) {
  return useQuery({
    queryKey: scoringKey(sourceId),
    queryFn: () => api.get<Assessment[]>(
      `/api/v1/risk/assessments?source_type=${sourceType}&source_id=${sourceId}`),
    // A route parameter that has not arrived yet would ask the server for the
    // assessments of nothing, which is a 422 drawn as a load failure.
    enabled: Boolean(sourceId),
  })
}

export function RiskScoringHistory({
  sourceType, sourceId, emptyNote, showHazard = false,
}: {
  sourceType: string
  sourceId: string
  /** What "none yet" should say in the reader's own terms. */
  emptyNote: string
  /** An incident is scored per hazard, so it says which; a hazard is itself the hazard. */
  showHazard?: boolean
}) {
  const scorings = useScorings(sourceType, sourceId)
  const frame = 'mt-3 border-t border-neutral-100 pt-3 text-xs'

  if (scorings.isLoading) {
    return <p className={`${frame} text-neutral-500`}>Loading…</p>
  }
  // ★ A failed read is not an absence. "Not scored yet" is a claim about the
  // record, and making it on the strength of a request that never answered is
  // how somebody scores the same incident twice.
  if (scorings.isError) {
    return (
      <div className={frame}>
        <LoadFailedNote what="The risk assessments" />
      </div>
    )
  }
  if (!scorings.data?.length) {
    return <p className={`${frame} text-neutral-500`}>{emptyNote}</p>
  }

  return (
    <ul className="mt-3 flex flex-col gap-2.5 border-t border-neutral-100 pt-3">
      {scorings.data.map((a) => (
        <li key={a.id} className="text-xs">
          <span className="flex flex-wrap items-center gap-2">
            <RatingBadge rating={a.rating} />
            <span className="text-neutral-500">
              judged on <span className="capitalize">{a.consequence_type.replace('_', ' ')}</span>
            </span>
            <span className="text-neutral-400">·</span>
            <span className="text-neutral-600">
              {a.assessed_by_name ?? 'Unknown'}, {formatDate(a.assessed_at)}
            </span>
          </span>
          {showHazard && a.hazard_label && (
            <span className="mt-1 block font-medium text-neutral-800">{a.hazard_label}</span>
          )}
          <span className="mt-1 block text-neutral-700">
            Likelihood {a.likelihood_code}
            {a.likelihood_label && ` — ${a.likelihood_label}`}
            {' · '}
            Severity {a.severity_level}
            {a.severity_label && ` — ${a.severity_label}`}
          </span>
          {a.residual_rating && (
            <span className="mt-0.5 block text-neutral-500">
              Once the controls are in: <span className="capitalize">{a.residual_rating}</span>
              {a.residual_likelihood_code && ` (${a.residual_likelihood_code}`}
              {a.residual_severity_level !== null && `/${a.residual_severity_level})`}
            </span>
          )}
          {a.controls && (
            <span className="mt-0.5 block text-neutral-600">Controls: {a.controls}</span>
          )}
        </li>
      ))}
    </ul>
  )
}
