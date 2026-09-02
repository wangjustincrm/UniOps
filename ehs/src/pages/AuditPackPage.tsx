/**
 * The evidence pack an auditor asks for.
 *
 * COR, ISO 45001 and a Ministry inspection all ask variations of one short
 * list: your incidents and what you did about them, your corrective actions
 * and whether they closed on time, your training, your committee minutes, your
 * inspections, your data sheets. None of it is new — it is the module, counted.
 *
 * Two rules decide how it is presented.
 *
 * **A rate over nothing is not a rate.** Zero of zero corrective actions closed
 * on time is not full compliance, and showing it as 100% is the most common way
 * a compliance dashboard lies. Where the server returns null, this page says
 * "none in this period" instead of a green number.
 *
 * **Breaches are named, not counted.** The statutory section lists every missed
 * deadline with its regulation, its due date and how late it was, because that
 * is the list an auditor will work down. A percentage would hide exactly the
 * rows they came to see.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Check, Printer } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { Card, PageHeader, inputClass, secondaryButton } from '@/components/ui'

interface Breach {
  kind: string; regulation: string | null; reference: string | null
  due_at: string; satisfied_at: string | null; late_by_hours: number
}
interface Pack {
  period: { from: string; to: string }
  generated_at: string
  incidents: Record<string, number | null>
  statutory: {
    total: number; met: number; missed: Breach[]
    still_open: { kind: string; reference: string | null; due_at: string }[]
    on_time_rate: number | null
  }
  actions: Record<string, number | null>
  training: Record<string, number | null>
  committee: Record<string, number | boolean | null>
  inspections: Record<string, number | null>
  documents: Record<string, number | null>
  high_risk_work: Record<string, number | null>
  emergency: Record<string, number | null>
  toolbox: Record<string, number | null>
}

const KIND_LABELS: Record<string, string> = {
  mol_48h: 'Ministry written report (48 hours)',
  wsib_form7: 'WSIB Form 7 (three business days)',
  jhsc_reply_21d: 'JHSC written reply (21 days)',
  policy_annual: 'Annual policy review',
  sds_3y: 'Safety data sheet review (three years)',
  cert_expiry: 'Certification expiry',
}

export default function AuditPackPage() {
  const year = new Date().getFullYear()
  const [from, setFrom] = useState(`${year}-01-01`)
  const [to, setTo] = useState(`${year + 1}-01-01`)

  const pack = useQuery({
    queryKey: ['audit-pack', from, to],
    queryFn: () => api.get<Pack>(
      `/api/v1/audit/evidence-pack?date_from=${from}&date_to=${to}`),
  })

  if (pack.isLoading) return <p className="p-4 text-sm text-neutral-500">Assembling…</p>
  const p = pack.data
  if (!p) return null

  return (
    <div className="p-4">
      <PageHeader
        title="Audit evidence"
        subtitle="Everything a COR, ISO 45001 or Ministry audit asks for, over a period"
      >
        <input type="date" className={`${inputClass} w-40`} value={from}
               onChange={(e) => setFrom(e.target.value)} aria-label="From" />
        <input type="date" className={`${inputClass} w-40`} value={to}
               onChange={(e) => setTo(e.target.value)} aria-label="To" />
        <button type="button" className={secondaryButton} onClick={() => window.print()}>
          <Printer className="h-4 w-4" aria-hidden />
          Print
        </button>
      </PageHeader>

      {/* ── The section they open with ──────────────────────────────────── */}
      <Statutory statutory={p.statutory} />

      <Section title="Incidents">
        <Figures rows={[
          ['Reported', p.incidents.total],
          ['Injuries', p.incidents.injuries],
          ['Lost time', p.incidents.lost_time],
          ['Reportable to the Ministry', p.incidents.mol_reportable],
          ['Near misses', p.incidents.near_misses],
        ]} />
        <Rates rows={[
          ['Injuries investigated', p.incidents.injury_investigation_rate,
           `${p.incidents.investigated_injuries} of ${p.incidents.injuries}`],
          ['Closed', p.incidents.closure_rate,
           `${p.incidents.closed} of ${p.incidents.total}`],
        ]} />
      </Section>

      <Section title="Corrective actions">
        <Figures rows={[
          ['Raised', p.actions.raised],
          ['Closed', p.actions.closed],
          ['Overdue right now', p.actions.overdue_now, true],
        ]} />
        <Rates rows={[
          ['Closed on time', p.actions.on_time_rate,
           `${p.actions.closed_on_time} of ${p.actions.closed}`],
          ['Effectiveness verified', p.actions.verification_rate,
           `${p.actions.verified} of ${p.actions.closed}`],
        ]} />
      </Section>

      <Section title="Training">
        <Figures rows={[
          ['Records added', p.training.records_added],
          ['Sessions held', p.training.sessions_held],
          ['Expired certifications', p.training.expired_certifications, true],
          ['Outstanding gaps', p.training.outstanding_gaps, true],
        ]} />
        <Rates rows={[
          ['Workforce compliant', p.training.compliance_rate,
           `${p.training.workers_compliant} of ${p.training.workers_considered}`],
        ]} />
      </Section>

      <Section title="Health and safety committee">
        <Figures rows={[
          ['Meetings held', p.committee.meetings as number],
          ['Minutes published', p.committee.minutes_published as number],
          ['Members', p.committee.members as number],
          ['Certified members', p.committee.certified_members as number],
        ]} />
        <p className={`mt-2 flex items-center gap-1.5 text-sm font-medium ${
          p.committee.worker_majority_met ? 'text-success-700' : 'text-warning-700'}`}>
          {p.committee.worker_majority_met
            ? <Check className="h-4 w-4" aria-hidden />
            : <AlertTriangle className="h-4 w-4" aria-hidden />}
          Worker majority (OHSA s.9(6)) —{' '}
          {p.committee.worker_majority_met ? 'met' : 'not met'}
          {' · '}{p.committee.worker_members as number} of{' '}
          {p.committee.members as number} are worker representatives
        </p>
        <Rates rows={[
          ['Recommendations answered', p.committee.reply_rate as number | null,
           `${p.committee.answered} of ${p.committee.recommendations}`],
        ]} />
      </Section>

      <Section title="Inspections and equipment">
        <Figures rows={[
          ['Inspections due', p.inspections.scheduled],
          ['Missed', p.inspections.missed, true],
          ['Equipment out of service', p.inspections.equipment_out_of_service, true],
        ]} />
        <Rates rows={[
          ['Inspections completed', p.inspections.completion_rate,
           `${p.inspections.completed} of ${p.inspections.scheduled}`],
          ['Equipment checks done', p.inspections.equipment_check_rate,
           `${p.inspections.equipment_checks_done} of ${p.inspections.equipment_checks_due}`],
        ]} />
      </Section>

      <Section title="High-risk work">
        <Figures rows={[
          ['Permits issued', p.high_risk_work.permits_issued],
          ['Expired without close-out', p.high_risk_work.permits_expired_without_closeout, true],
          ['Job analyses in use', p.high_risk_work.active_job_analyses],
          ['Open hazards', p.high_risk_work.open_hazards],
          ['Rated critical', p.high_risk_work.critical_hazards, true],
        ]} />
        <Rates rows={[
          ['Permits closed out', p.high_risk_work.permit_closeout_rate,
           `${p.high_risk_work.permits_closed} of ${p.high_risk_work.permits_issued}`],
        ]} />
      </Section>

      <Section title="Documents and chemicals">
        <Figures rows={[
          ['Policies published', p.documents.policies_published],
          ['Past their review date', p.documents.review_overdue, true],
          ['Hazardous products', p.documents.chemicals],
          ['Data sheets past review', p.documents.sds_review_overdue, true],
        ]} />
        <Rates rows={[
          ['Products with a current data sheet', p.documents.sds_coverage_rate,
           `${p.documents.with_current_sds} of ${p.documents.chemicals}`],
        ]} />
      </Section>

      <Section title="Emergency preparedness">
        <Figures rows={[
          ['Drills held', p.emergency.drills_held],
          ['Muster failed', p.emergency.muster_failed, true],
          ['Muster not answered', p.emergency.muster_unanswered, true],
          ['Current first aiders', p.emergency.current_first_aiders],
          ['Lapsed first aiders', p.emergency.lapsed_first_aiders, true],
        ]} />
        {p.emergency.average_evacuation_seconds !== null && (
          <p className="mt-2 text-sm text-neutral-700">
            Average time to evacuate:{' '}
            <span className="font-mono font-semibold">
              {Math.floor((p.emergency.average_evacuation_seconds as number) / 60)}:
              {String((p.emergency.average_evacuation_seconds as number) % 60).padStart(2, '0')}
            </span>
          </p>
        )}
      </Section>

      <Section title="Toolbox talks">
        <Figures rows={[
          ['Topics issued', p.toolbox.topics],
          ['Team deliveries', p.toolbox.deliveries],
        ]} />
        <Rates rows={[
          ['Signed off', p.toolbox.completion_rate,
           `${p.toolbox.delivered} of ${p.toolbox.deliveries}`],
        ]} />
      </Section>

      <p className="mt-6 text-xs text-neutral-500">
        Covering {formatDate(p.period.from)} to {formatDate(p.period.to)}. Assembled{' '}
        {formatDate(p.generated_at)} from the live records — nothing here is
        entered by hand.
      </p>
    </div>
  )
}

function Statutory({ statutory }: { statutory: Pack['statutory'] }) {
  const clean = statutory.missed.length === 0

  return (
    <section className="mb-6">
      <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-neutral-400">
        Statutory deadlines
      </h2>

      {statutory.total === 0 ? (
        <Card>
          <p className="text-sm text-neutral-600">
            No legal clocks started in this period. That is not a compliance
            figure — it means nothing happened that started one.
          </p>
        </Card>
      ) : clean ? (
        <Card className="!border-success-500 !bg-success-50">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-success-700">
            <Check className="h-4 w-4" aria-hidden />
            All {statutory.total} met on time
          </p>
        </Card>
      ) : (
        <div className="rounded-xl border-l-4 border-danger-600 bg-danger-50 p-3.5">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-danger-700">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            {statutory.missed.length} of {statutory.total} missed
          </p>
          <ul className="mt-2 flex flex-col gap-2">
            {statutory.missed.map((m, i) => (
              <li key={`${m.reference}-${m.kind}-${i}`}
                  className="rounded-lg bg-white p-2.5">
                <p className="text-sm font-semibold text-neutral-900">
                  {KIND_LABELS[m.kind] ?? m.kind}
                  {m.reference && <span className="ml-1.5 font-mono text-xs text-neutral-500">
                    {m.reference}
                  </span>}
                </p>
                <p className="mt-0.5 text-xs text-neutral-600">
                  {m.regulation && <>{m.regulation} · </>}
                  due {new Date(m.due_at).toLocaleString()}
                  {' · '}
                  <span className="font-semibold text-danger-700">
                    {m.satisfied_at
                      ? `filed ${Math.round(m.late_by_hours)} hours late`
                      : `never filed — ${Math.round(m.late_by_hours)} hours overdue`}
                  </span>
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {statutory.still_open.length > 0 && (
        <p className="mt-2 text-xs text-neutral-600">
          {statutory.still_open.length} clock
          {statutory.still_open.length === 1 ? ' is' : 's are'} still running and
          not yet due.
        </p>
      )}
    </section>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-5">
      <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-neutral-400">
        {title}
      </h2>
      <Card>{children}</Card>
    </section>
  )
}

function Figures({ rows }: { rows: [string, number | null | undefined, boolean?][] }) {
  return (
    <ul className="flex flex-wrap gap-x-6 gap-y-3">
      {rows.map(([label, value, warnIfAny]) => (
        <li key={label}>
          <p className="text-xs text-neutral-500">{label}</p>
          <p className={`font-mono text-xl font-bold tabular-nums ${
            warnIfAny && (value ?? 0) > 0 ? 'text-danger-700' : 'text-neutral-900'}`}>
            {value ?? 0}
          </p>
        </li>
      ))}
    </ul>
  )
}

/**
 * A rate, or an honest sentence when there is nothing to compute it over.
 *
 * The server returns null rather than zero for an empty denominator, and this
 * says so in words. "0%" and "no corrective actions were closed in this
 * period" look the same on a dashboard and mean completely different things.
 */
function Rates({ rows }: { rows: [string, number | null | undefined, string][] }) {
  return (
    <ul className="mt-3 flex flex-col gap-1.5 border-t border-neutral-100 pt-3">
      {rows.map(([label, rate, detail]) => (
        <li key={label} className="flex flex-wrap items-baseline gap-2 text-sm">
          <span className="min-w-0 flex-1 text-neutral-700">{label}</span>
          {rate === null || rate === undefined ? (
            <span className="text-xs text-neutral-500">none in this period</span>
          ) : (
            <>
              <span className={`font-mono font-bold tabular-nums ${
                rate >= 90 ? 'text-success-700'
                  : rate >= 70 ? 'text-warning-700' : 'text-danger-700'}`}>
                {rate}%
              </span>
              <span className="text-xs text-neutral-500">{detail}</span>
            </>
          )}
        </li>
      ))}
    </ul>
  )
}
