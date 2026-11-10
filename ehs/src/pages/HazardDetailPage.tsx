/**
 * One hazard, every field, read-only.
 *
 * ★ Round 12, HAZ-04 / 09 / 10 and the default Kirk accepted: "Workers,
 * supervisors and HSE get one read-only hazard page with every field." There
 * was no page for a single hazard: the register showed a summary line and the
 * edit form, so a worker saw neither the causes nor the corrective actions
 * (14 causes and 8 CAPAs on dev were on file and on no screen), and a link to
 * a hazard dropped somebody into the whole register.
 *
 * Someone who cannot write hazards reaches one once it has been assessed
 * (Q11); before that the server answers 404.
 */
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTabTitle } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'
import { api, ApiError } from '@/lib/api'
import { formatDate, formatDateTime } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { useEquipmentName } from '@/components/EquipmentSelect'
import { ActionHistory } from '@/components/ActionHistory'
import { ChangeHistory } from '@/components/ChangeHistory'
import { RecordPhotos } from '@/components/RecordPhotos'
import { RatingBadge, RiskScoringHistory } from '@/components/RiskScoring'
import { RiskAssessmentDialog } from '@/components/RiskAssessmentDialog'
import { Card, LoadFailedNote, LoadProblem, SectionTitle, primaryButton } from '@/components/ui'
import { CONTROL_LEVELS, hazardKinds, type Hazard } from '@/pages/HazardsPage'

interface Cause { id: string; cause_type: string; label: string; note: string | null }
interface CauseTree { immediate: Cause[]; root: Cause[] }

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[11rem_1fr] sm:gap-3">
      <dt className="text-xs font-medium text-neutral-500">{label}</dt>
      <dd className="whitespace-pre-wrap text-sm text-neutral-900">{children}</dd>
    </div>
  )
}

export default function HazardDetailPage() {
  const { id = '' } = useParams()
  const { can } = usePermissions()
  const mayWrite = can('ehs.action.write')
  const [assessing, setAssessing] = useState(false)
  const hazard = useQuery({
    queryKey: ['hazard', id],
    queryFn: () => api.get<Hazard>(`/api/v1/hazards/${id}`),
    enabled: Boolean(id),
  })
  const causes = useQuery({
    queryKey: ['hazard', id, 'causes'],
    queryFn: () => api.get<CauseTree>(`/api/v1/hazards/${id}/causes`),
    enabled: Boolean(id) && hazard.isSuccess,
  })
  useTabTitle(ehsRoutes, hazard.data?.hazard_no)
  const equipment = useEquipmentName(hazard.data?.equipment_id)

  if (hazard.isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  // ★ Q11: someone who cannot write hazards is answered 404 until it has been
  // assessed. "No longer exists" would be untrue; say why it is not shown.
  if (hazard.isError && !mayWrite && hazard.error instanceof ApiError && hazard.error.status === 404) {
    return (
      <div className="p-4 text-sm text-neutral-600">
        This hazard is shown once it has been assessed.{' '}
        <Link to="/hazards" className="font-medium text-primary-700 hover:underline">Hazard register</Link>
      </div>
    )
  }
  if (hazard.isError || !hazard.data) return <LoadProblem error={hazard.error ?? null} noun="hazard" />
  const h = hazard.data
  const kinds = hazardKinds(h)
  const level = CONTROL_LEVELS.find((c) => c.key === h.control_level)?.label ?? h.control_level

  return (
    <div className="mx-auto max-w-3xl p-4">
      <Link to="/hazards" className="text-xs font-medium text-primary-700 hover:underline">
        ← Hazard register
      </Link>
      <div className="mb-3 mt-2 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-xs text-neutral-500">{h.hazard_no}</p>
          <h1 className="text-lg font-bold text-neutral-900">{h.title}</h1>
        </div>
        <div className="flex flex-col items-end gap-1">
          <RatingBadge rating={h.current_rating} />
          {h.residual_rating && (
            <span className="text-[11px] text-neutral-500">
              after controls: <span className="capitalize">{h.residual_rating}</span>
            </span>
          )}
          <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold capitalize text-neutral-600">
            {h.status}
          </span>
        </div>
      </div>
      {mayWrite && (
        <div className="mb-3 flex gap-2">
          <Link to={`/hazards?edit=${h.id}`}
                className="inline-flex min-h-[40px] items-center rounded-lg border border-neutral-300 bg-white px-3 text-sm font-medium text-neutral-700">
            Edit
          </Link>
          <button type="button" className={primaryButton} onClick={() => setAssessing(true)}>
            {h.current_rating ? 'Reassess' : 'Assess the risk'}
          </button>
        </div>
      )}

      <Card>
        <dl className="flex flex-col gap-2.5">
          <Row label="Recorded">
            {formatDateTime(h.created_at)}{h.created_by_name && ` by ${h.created_by_name}`}
          </Row>
          {h.revised_at && (
            <Row label="Last revised on">
              {formatDateTime(h.revised_at)}{h.revised_by_name && ` by ${h.revised_by_name}`}
            </Row>
          )}
          <Row label="Owner">{h.owner_name ?? '—'}</Row>
          <Row label="Kind">{kinds.length ? kinds.join(', ') : '—'}</Row>
          <Row label="Where">{h.location_path ?? '—'}{h.department_name && ` · ${h.department_name}`}</Row>
          <Row label="Equipment">{equipment ?? '—'}</Row>
          <Row label="Description">{h.description || '—'}</Row>
          <Row label="What was found">{h.findings || '—'}</Row>
          <Row label="Controls">
            {(h.hazard_controls ?? []).length > 0 && (
              <span className="block">{h.hazard_controls!.map((c) => c.label).join(', ')}</span>
            )}
            {h.existing_controls && <span className="block">{h.existing_controls}</span>}
            {!(h.hazard_controls ?? []).length && !h.existing_controls && '—'}
          </Row>
          <Row label="Kind of control">{level ?? 'Not stated'}</Row>
          <Row label="Identified">{h.identified_on ? formatDate(h.identified_on) : '—'}</Row>
          <Row label="Review due">{h.review_due_on ? formatDate(h.review_due_on) : '—'}</Row>
        </dl>
      </Card>

      <section className="mt-5">
        <SectionTitle className="mb-2">Causes</SectionTitle>
        {causes.isError ? <LoadFailedNote what="the causes" /> : (
          <div className="grid gap-3 sm:grid-cols-2">
            {(['immediate', 'root'] as const).map((kind) => (
              <div key={kind}>
                <p className="mb-1 text-xs font-semibold text-neutral-700">
                  {kind === 'immediate' ? 'Immediate causes' : 'Root causes'}
                </p>
                {(causes.data?.[kind] ?? []).length ? (
                  <ul className="flex flex-col gap-1 text-sm text-neutral-800">
                    {causes.data![kind].map((c) => (
                      <li key={c.id}>
                        {c.label}
                        {c.note && <span className="block text-xs text-neutral-500">{c.note}</span>}
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-sm text-neutral-500">None recorded.</p>}
              </div>
            ))}
          </div>
        )}
      </section>

      <ActionHistory sourceType="hazard" sourceId={h.id} title="Corrective actions" />

      <section className="mt-5">
        <SectionTitle className="mb-2">Risk assessments</SectionTitle>
        <RiskScoringHistory sourceType="hazard" sourceId={h.id} showHazard
          emptyNote="This hazard has not been scored against the matrix yet." />
      </section>

      <RecordPhotos type="hazard" ownerId={h.id} canAdd={mayWrite} />

      <section className="mt-5">
        <ChangeHistory table="ehs_hazards" rowId={h.id} />
      </section>

      {assessing && (
        <RiskAssessmentDialog sourceType="hazard" sourceId={h.id} onClose={() => setAssessing(false)}
          hazard={{ id: h.id, hazard_no: h.hazard_no, title: h.title, kinds }} />
      )}
    </div>
  )
}
