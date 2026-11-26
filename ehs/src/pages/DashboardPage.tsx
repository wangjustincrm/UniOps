/**
 * The management dashboard.
 *
 * Round 9 (q9-other) set the order, in HSE's words: "5 boxes across the top —
 * days since last medical incident, total medical incidents recorded for the
 * year, total first aid, total medical aid, total lost time"; "move the
 * injuries this year graphic down below the 5 boxes"; "leave the recordable
 * incident rate, lost time hours, incident cost after the injuries this year
 * box"; near misses "on its own later down the dashboard"; and in separate
 * boxes the monthly scorecards and a chart by supervisor. Round 10 settled
 * that the colours and the chart styles are HSE's, set once for everybody
 * under Settings → Dashboard.
 *
 * Kept from before:
 *
 *   **Days since the last medical incident counts injuries only.** Counting
 *   everything meant reporting a good catch reset it to zero, which punishes
 *   the reporting it exists to encourage.
 *
 *   **A missing figure says what is missing.** A zero injury rate reads like a
 *   good year rather than like a missing divisor; a cost that only some
 *   incidents carry says how many.
 *
 *   **Every number that is a count opens the records it counted**, through the
 *   register's own filters and its own access rules.
 *
 * Charts are divs and plain SVG (components/dashboard/charts.tsx): no charting
 * library, and these print.
 */
import TabLink from '@/components/TabLink'
import { DashboardTitles, MonthChange } from '@/components/dashboard/MonthChange'
import { Fragment, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { TrendingUp } from 'lucide-react'
import { api } from '@/lib/api'
import { formatAreaPath } from '@/lib/areaPath'
import { SectionTitle } from '@/components/ui'
import { formatDate } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import {
  Bars, BodyMap, Donut, Legend, MONTH_NAMES, MonthColumns, StackedColumns,
  type CountRow,
} from '@/components/dashboard/charts'

interface Headline {
  year: number; incidents: number; injuries: number; first_aid: number; medical: number
  lost_time: number; recordable: number; rir: number | null
  lost_time_rate: number | null; dart_rate: number | null
  days_since_last_incident: number | null
  cost_total: number; cost_average: number | null; cost_known_count: number
  lost_time_hours: number; rir_note: string | null
}
interface MonthRow { month: string; first_aid: number; medical_aid: number; lost_time: number }
interface NearMissRow { month: string; this_year: number; last_year: number }
interface BodyRow extends CountRow { region?: string | null }
interface ResolutionRow {
  month: string; reported: number; closed: number; average_days: number | null; still_open: number
}
interface CapaRow { month: string; created: number; resolved: number }
interface AssessmentRow { month: string; count: number; capa_resolved: number }
type Tone = 'good' | 'warn' | 'bad' | null
interface MonthChangeData {
  month: string; previous: string
  this: Record<string, number>; last: Record<string, number>
}
interface Dashboard {
  headline: Headline
  month_change: MonthChangeData
  medical_events: MonthRow[]
  near_misses: NearMissRow[]
  body_parts: BodyRow[]
  body_map: { regions: Record<string, number>; off_picture: BodyRow[] }
  injured_departments: CountRow[]
  injured_locations: CountRow[]
  resolution: ResolutionRow[]
  capa: CapaRow[]
  assessments: AssessmentRow[]
  jhsc: {
    available: boolean
    reason: string | null
    months?: { id: string; month: string; held_on: string | null; present: number; invited: number }[]
    members?: number
  }
  tones: Record<string, Tone>
  chart_styles: Record<string, string>
}
interface Settings {
  thresholds: Record<string, { good: number; bad: number }>
  metrics: { key: string; label: string; higher_is_better: boolean }[]
  regions: { key: string; label: string }[]
}
interface SupervisorColumn {
  supervisor_id: string | null; name: string
  near_miss: number; hazard_assessment: number; toolbox_talk: number
}

/** Severity light to dark, so the serious end of every bar is the same colour
 *  and the same side throughout the page. */
const SEVERITY = [
  { key: 'first_aid', label: 'First aid', cls: 'bg-primary-300' },
  { key: 'medical_aid', label: 'Medical aid', cls: 'bg-warning-400' },
  { key: 'lost_time', label: 'Lost time', cls: 'bg-danger-500' },
] as const

/* What a tone looks like. The word travels with the colour (in the title and
   for screen readers), so the box does not rely on colour alone. */
const TONE_STYLE: Record<string, { box: string; value: string; word: string }> = {
  good: { box: 'border-success-300 bg-success-50', value: 'text-success-800', word: 'On target' },
  warn: { box: 'border-warning-300 bg-warning-50', value: 'text-warning-800', word: 'Watch' },
  bad: { box: 'border-danger-300 bg-danger-50', value: 'text-danger-700', word: 'Off target' },
}
const NO_TONE = { box: 'border-neutral-200 bg-white', value: 'text-neutral-900', word: '' }

/**
 * A link from a figure to the records it was counted from.
 *
 * ★ The parameters are the server's own, and they say the same thing the
 * figure's own query says — same year boundary, same definition of
 * "recordable", same null department. A drill-down that lands on a list
 * filtered by something merely *near* the number is worse than none.
 */
function drillTo(year: number, via: string, params: Record<string, string | string[] | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    for (const one of Array.isArray(v) ? v : [v]) if (one) q.append(k, one)
  }
  q.set('occurred_from', `${year}-01-01`)
  q.set('occurred_to', `${year}-12-31`)
  q.set('via', via)
  return `/incidents?${q.toString()}`
}

/** The same, for one month. Day 0 of the next month is this month's last. */
function monthDrill(year: number, monthIndex: number, via: string,
                    params: Record<string, string | string[] | undefined>): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  const last = new Date(year, monthIndex + 1, 0).getDate()
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    for (const one of Array.isArray(v) ? v : [v]) if (one) q.append(k, one)
  }
  q.set('occurred_from', `${year}-${pad(monthIndex + 1)}-01`)
  q.set('occurred_to', `${year}-${pad(monthIndex + 1)}-${pad(last)}`)
  q.set('via', `${via} ${year}`)
  return `/incidents?${q.toString()}`
}

/** What "still open" means in the register: sent, and not closed. */
const OPEN_STATUSES = ['submitted', 'under_investigation', 'pending_closure']

function DrillLink({ to, children, className = '' }: {
  to: string; children: React.ReactNode; className?: string
}) {
  return (
    <TabLink to={to}
          className={`rounded-sm underline decoration-transparent underline-offset-2 transition-colors hover:decoration-current focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500 ${className}`}>
      {children}
    </TabLink>
  )
}

function Panel({ title, children, note, className = '', aside }: {
  title: string; children: React.ReactNode; note?: string; className?: string; aside?: React.ReactNode
}) {
  return (
    <section className={`rounded-xl border border-neutral-200 bg-white p-4 ${className}`}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <SectionTitle className="mb-0">{title}</SectionTitle>
        {aside}
      </div>
      {children}
      {note && <p className="mt-2.5 text-[11px] leading-relaxed text-neutral-400">{note}</p>}
    </section>
  )
}

/** "Green above 15, red below 5" — what the colour on a box means, in words. */
function thresholdWords(key: string, settings: Settings | undefined): string | null {
  const pair = settings?.thresholds[key]
  const metric = settings?.metrics.find((m) => m.key === key)
  if (!pair || !metric) return null
  return metric.higher_is_better
    ? `Green above ${pair.good}, red below ${pair.bad}, yellow between.`
    : `Green below ${pair.good}, red above ${pair.bad}, yellow between.`
}

function Box({ value, label, tone, to, explain, empty, change }: {
  value: number | null; label: string; tone: Tone; to?: string
  explain: string | null
  /** Said instead of a number when there is none. */
  empty?: string
  /** UAT 14: this month's count and its arrow, top right. */
  change?: React.ReactNode
}) {
  const t = tone ? TONE_STYLE[tone] : NO_TONE
  return (
    <div className={`relative flex min-h-[104px] flex-col justify-between rounded-xl border p-4 ${t.box}`}
         title={[t.word, explain].filter(Boolean).join(' — ') || undefined}>
      {change && <div className="absolute right-3 top-2.5">{change}</div>}
      <p className={`text-4xl font-bold tabular-nums leading-none ${t.value}`}>
        {value === null ? (
          <span className="text-base font-semibold text-neutral-600">{empty ?? '—'}</span>
        ) : to ? <DrillLink to={to}>{value}</DrillLink> : value}
      </p>
      <p className="mt-2 text-[11px] font-semibold uppercase tracking-wide text-neutral-600">
        {label}
        {t.word && <span className="sr-only"> — {t.word}</span>}
      </p>
    </div>
  )
}

function Stat({ value, label, sub, subTo }: {
  value: string | number; label: string; sub?: string; subTo?: string
}) {
  return (
    <div>
      <p className="text-xl font-bold tabular-nums leading-none text-neutral-900">{value}</p>
      <p className="mt-1 text-[11px] font-medium uppercase tracking-wide text-neutral-500">{label}</p>
      {sub && (
        <p className="mt-0.5 text-[11px] text-neutral-400">
          {subTo ? <DrillLink to={subTo}>{sub}</DrillLink> : sub}
        </p>
      )}
    </div>
  )
}

function EmptyNote({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-neutral-200 px-3 py-6 text-center text-sm text-neutral-500">
      {children}
    </p>
  )
}

const SUPERVISOR_PARTS = [
  { key: 'near_miss', label: 'Near misses', cls: 'bg-warning-400' },
  { key: 'hazard_assessment', label: 'Hazard assessments', cls: 'bg-primary-300' },
  { key: 'toolbox_talk', label: 'Toolbox talks', cls: 'bg-primary-700' },
]

/** Round 9: "a stacked column graph … grouped by supervisor for the current
 *  month. create a button on the graph to see last months data." HSE only. */
function SupervisorPanel() {
  const [ago, setAgo] = useState<0 | 1>(0)
  const now = new Date()
  const at = new Date(now.getFullYear(), now.getMonth() - ago, 1)
  const month = `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, '0')}`
  const q = useQuery({
    queryKey: ['dashboard', 'supervisor-activity', month],
    queryFn: () => api.get<{ month: string; rows: SupervisorColumn[]; unassigned: SupervisorColumn | null }>(
      `/api/v1/dashboard/supervisor-activity?month=${month}`),
  })
  const columns = [...(q.data?.rows ?? []), ...(q.data?.unassigned ? [q.data.unassigned] : [])]
    .map((c) => ({ key: c.supervisor_id ?? 'none', name: c.name,
                   values: { near_miss: c.near_miss, hazard_assessment: c.hazard_assessment,
                             toolbox_talk: c.toolbox_talk } }))
  return (
    <Panel title={`By supervisor — ${MONTH_NAMES[at.getMonth()]} ${at.getFullYear()}`}
           className="lg:col-span-2"
           note={'Counted for the person each record\'s author reports to now (Portal → Admin → Users). '
                 + 'A toolbox talk counts for the supervisor who held it. Anonymous near misses, and '
                 + 'records whose person has no supervisor set, are under "No supervisor set". Only HSE sees this chart.'}
           aside={
             <div className="flex gap-1" role="group" aria-label="Which month">
               {([0, 1] as const).map((n) => (
                 <button key={n} type="button" onClick={() => setAgo(n)} aria-pressed={ago === n}
                         className={`min-h-[32px] rounded-md px-2.5 text-xs font-semibold ${ago === n
                           ? 'bg-primary-600 text-white' : 'text-neutral-600 hover:bg-neutral-100'}`}>
                   {n === 0 ? 'This month' : 'Last month'}
                 </button>
               ))}
             </div>
           }>
      {q.isPending ? <p className="text-sm text-neutral-500">Loading…</p>
        : q.isError ? <EmptyNote>Could not load this chart.</EmptyNote>
          : (
            <StackedColumns columns={columns} parts={SUPERVISOR_PARTS}
                            empty={<EmptyNote>Nothing recorded for this month yet.</EmptyNote>} />
          )}
    </Panel>
  )
}

export default function DashboardPage() {
  const [hours, setHours] = useState('')
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['dashboard', hours],
    queryFn: () => api.get<Dashboard>(
      `/api/v1/dashboard${hours ? `?hours_worked=${hours}` : ''}`),
  })
  const settings = useQuery({
    queryKey: ['dashboard', 'settings'],
    queryFn: () => api.get<Settings>('/api/v1/dashboard/settings'),
  })
  const { can } = usePermissions()
  const isHse = can('ehs.settings.manage')

  // Which panels this person wants, and in what order. The library lives on
  // the server so a panel added later appears in everybody's chooser.
  const queryClient = useQueryClient()
  const layout = useQuery({
    queryKey: ['dashboard-layout'],
    queryFn: () => api.get<{ panels: string[]; available: { key: string; label: string }[] }>(
      '/api/v1/dashboard/layout'),
  })
  // ★ How much area work nobody has been named for.
  const unowned = useQuery({
    queryKey: ['unattributed'],
    queryFn: () => api.get<{ count: number; examples: string[] }>(
      '/api/v1/actions/unattributed'),
  })
  const [choosing, setChoosing] = useState(false)
  // The chart by supervisor is HSE's; for anybody else it is not on offer.
  const offered = (key: string) => key !== 'supervisor_activity' || isHse
  const order = (layout.data?.panels ?? []).filter(offered)
  const saveLayout = useMutation({
    mutationFn: (panels: string[]) =>
      api.put('/api/v1/dashboard/layout', { panels }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard-layout'] }),
  })

  // ★ Every hook above the two early returns below.
  if (isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  // ★ A failure is said, not drawn as an empty page — an empty dashboard and
  // one that never arrived look the same otherwise.
  if (isError || !data) {
    return (
      <div className="p-4">
        <div className="mb-4"><DashboardTitles current="hse" /></div>
        <div role="alert" className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2.5">
          <p className="min-w-0 flex-1 text-sm text-danger-800">Could not load the dashboard.</p>
          <button type="button" onClick={() => refetch()} disabled={isFetching}
                  className="min-h-[36px] rounded-md bg-white px-3 text-sm font-semibold text-danger-800 ring-1 ring-danger-300 hover:bg-danger-100 disabled:opacity-50">
            {isFetching ? 'Trying…' : 'Try again'}
          </button>
        </div>
      </div>
    )
  }

  const h = data.headline
  const year = h.year
  // ★ UAT 14: this month's count against last month's, on each figure.
  const mc = data.month_change
  // UAT 15, I and UAT 16, O: "more someone was hurt, or equipment was involved
  // incidents is worse, more near misses recorded and more hazard assessments
  // are good. more corrective actions is good."
  const UP_IS_GOOD = new Set(['near_misses', 'assessments', 'capa_raised'])
  const change = (key: string, size: 'box' | 'tile' = 'box') => (
    <MonthChange now={mc.this[key] ?? 0} before={mc.last[key] ?? 0} month={mc.month} previous={mc.previous}
                 upIsGood={UP_IS_GOOD.has(key)} size={size} />)
  const nearThis = data.near_misses.reduce((n, r) => n + r.this_year, 0)
  const nearLast = data.near_misses.reduce((n, r) => n + r.last_year, 0)
  const thisMonth = new Date().getFullYear() === year ? new Date().getMonth() : 11
  const resolutionNow = data.resolution[thisMonth]
  const regionNames = Object.fromEntries((settings.data?.regions ?? []).map((r) => [r.key, r.label]))

  const injuryDrill = (r: CountRow, by: 'location_id' | 'department_id') => drillTo(
    year, `injuries in ${r.label}`,
    r.id ? { injured_only: 'true', [by]: r.id }
      : by === 'department_id' ? { injured_only: 'true', no_department: 'true' } : { injured_only: 'true' })
  const categorical = (rows: CountRow[], style: string | undefined, linkFor: (r: CountRow) => string) =>
    style === 'circle' ? <Donut rows={rows} linkFor={linkFor} /> : <Bars rows={rows} linkFor={linkFor} />

  const panels: Record<string, React.ReactNode> = {
    injuries_by_month: (
      <Panel title="Injuries by month" className="lg:col-span-2" aside={change('injuries', 'tile')}>
        <MonthColumns
          rows={data.medical_events.map((r) => ({ ...r }))}
          series={[{ key: 'first_aid', label: 'First aid', cls: 'bg-primary-300' },
                   { key: 'medical_aid', label: 'Medical aid', cls: 'bg-warning-400' },
                   { key: 'lost_time', label: 'Lost time', cls: 'bg-danger-500' }]}
          height="h-32" stacked
          linkFor={(_, i) => monthDrill(year, i, `injuries in ${MONTH_NAMES[i]}`, { injured_only: 'true' })} />
      </Panel>
    ),
    near_misses: (
      <Panel title={`Near misses recorded — ${nearThis}`}
             aside={<span className="flex items-baseline gap-3">
               {nearLast > 0 && <span className="text-xs text-neutral-500">{nearLast} last year</span>}
               {change('near_misses', 'tile')}
             </span>}
             note="A rising line here is good — it means people are reporting what nearly happened.">
        <MonthColumns
          rows={data.near_misses}
          series={[{ key: 'this_year', label: 'This year', cls: 'bg-primary-500' },
                   { key: 'last_year', label: 'Last year', cls: 'bg-neutral-200' }]}
          linkFor={(_, i) => monthDrill(year, i, `near misses in ${MONTH_NAMES[i]}`, { form_kind: 'near_miss' })} />
      </Panel>
    ),
    resolution: (
      <Panel title="Average days to resolve incidents"
             note={'Days from the report being sent to the incident being closed (the last close, if it was '
                   + 'reopened), averaged over the incidents that happened that month and are closed. '
                   + 'Ones still open are counted beside it, not as zero days.'}>
        <div className="mb-3 flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <p className={`text-3xl font-bold tabular-nums leading-none ${
            data.tones.resolution_days ? TONE_STYLE[data.tones.resolution_days].value : 'text-neutral-900'}`}
             title={thresholdWords('resolution_days', settings.data) ?? undefined}>
            {resolutionNow.average_days ?? '—'}
          </p>
          <p className="text-xs text-neutral-600">
            days, {MONTH_NAMES[thisMonth]} · {resolutionNow.closed} closed
            {resolutionNow.still_open > 0 && (
              <> · <DrillLink to={monthDrill(year, thisMonth, `incidents still open from ${MONTH_NAMES[thisMonth]}`,
                                             { status: OPEN_STATUSES })}>
                {resolutionNow.still_open} still open</DrillLink></>
            )}
          </p>
        </div>
        <MonthColumns
          rows={data.resolution.map((r) => ({ ...r, days: r.average_days ?? 0 }))}
          series={[{ key: 'days', label: 'Average days', cls: 'bg-primary-500' }]}
          titleFor={(r, i) => `${MONTH_NAMES[i]}: ${r.average_days ?? 'none closed'}${
            r.average_days !== null ? ' days' : ''} · ${r.closed} closed, ${r.still_open} still open`}
          linkFor={(_, i) => monthDrill(year, i, `incidents in ${MONTH_NAMES[i]}`, {})} />
      </Panel>
    ),
    capa: (
      <Panel title="Corrective actions raised and closed" aside={change('capa_raised', 'tile')}
             note="Raised in the month, and closed in the month — closed means closed, not just marked done.">
        <MonthColumns
          rows={data.capa}
          series={[{ key: 'created', label: 'Raised', cls: 'bg-warning-400' },
                   { key: 'resolved', label: 'Closed', cls: 'bg-success-500' }]} />
      </Panel>
    ),
    assessments: (
      <Panel title={`Hazard assessments completed — ${data.assessments.reduce((n, r) => n + r.count, 0)}`}
             aside={change('assessments', 'tile')}>
        <MonthColumns
          rows={data.assessments}
          // ★ UAT 14: "add an additional bar for the number of hazard capa's
          // resolved just like the corrective actions."
          series={[{ key: 'count', label: 'Assessments', cls: 'bg-primary-500' },
                   { key: 'capa_resolved', label: 'Hazard corrective actions closed', cls: 'bg-success-500' }]} />
      </Panel>
    ),
    supervisor_activity: isHse ? <SupervisorPanel /> : null,
    injured_locations: (
      <Panel title="Where injuries are happening">
        {data.injured_locations.length === 0 ? <EmptyNote>No injuries recorded this year.</EmptyNote>
          : categorical(
            data.injured_locations.map((r) => ({ ...r, label: formatAreaPath(r.label) ?? r.label })),
            data.chart_styles.injured_locations, (r) => injuryDrill(r, 'location_id'))}
      </Panel>
    ),
    injured_departments: (
      <Panel title="Injured by department">
        {data.injured_departments.length === 0 ? <EmptyNote>No injuries recorded this year.</EmptyNote>
          : categorical(data.injured_departments, data.chart_styles.injured_departments,
                        (r) => injuryDrill(r, 'department_id'))}
      </Panel>
    ),
    body_parts: (
      <Panel title="Part of the body affected">
        {data.body_parts.length === 0 ? (
          <EmptyNote>No body parts recorded. These are entered in Section B of an injury report.</EmptyNote>
        ) : (() => {
          const linkFor = (r: CountRow) => drillTo(year, `injuries to the ${r.label.toLowerCase()}`,
                                                   { body_part_label: r.label })
          const style = data.chart_styles.body_parts
          return style === 'body'
            ? <BodyMap regions={data.body_map.regions} names={regionNames} rows={data.body_parts}
                       offPicture={data.body_map.off_picture} linkFor={linkFor} />
            : categorical(data.body_parts, style, linkFor)
        })()}
      </Panel>
    ),
    jhsc_attendance: (
      <Panel title="JHSC meeting attendance" className="lg:col-span-2"
             note="OHSA s.9 wants the committee meeting and a record of who was there.">
        {data.jhsc.available && (data.jhsc.months?.length ?? 0) > 0 ? (
          <ul className="flex flex-col gap-2">
            {data.jhsc.months!.map((m) => {
              const invited = Math.max(m.invited, 1)
              const pct = Math.round((m.present / invited) * 100)
              return (
                // ★ The meeting, not the month: two in one month are two rows.
                <li key={m.id} className="flex items-center gap-2">
                  <span className="w-24 shrink-0 text-right text-xs text-neutral-600">
                    {MONTH_NAMES[Number(m.month) - 1] ?? m.month}
                    {m.held_on && (
                      <span className="block text-[11px] text-neutral-400">{formatDate(m.held_on)}</span>
                    )}
                  </span>
                  <span className="flex h-4 flex-1 overflow-hidden rounded-sm bg-neutral-100">
                    <span className={pct >= 50 ? 'bg-success-500' : 'bg-warning-400'}
                          style={{ width: `${(m.present / invited) * 100}%` }}
                          title={`${m.present} of ${m.invited} attended`} />
                  </span>
                  <span className="w-16 text-xs font-semibold tabular-nums text-neutral-800">
                    {m.present}/{m.invited}
                  </span>
                </li>
              )
            })}
          </ul>
        ) : (
          <div className="flex items-start gap-2.5 rounded-lg border border-dashed border-neutral-300 p-4">
            <TrendingUp className="mt-0.5 h-4 w-4 shrink-0 text-neutral-400" aria-hidden />
            <p className="text-sm text-neutral-600">
              {data.jhsc.reason ?? 'No meeting has been recorded yet this year.'}
            </p>
          </div>
        )}
      </Panel>
    ),
  }

  const tones = data.tones
  return (
    <div className="p-4">
      <header className="mb-4 flex flex-wrap items-baseline gap-x-3">
        <DashboardTitles current="hse" />
        <span className="text-sm text-neutral-500">{year}</span>
      </header>

      {/* ── The five boxes ─────────────────────────────────────────────────── */}
      <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <div className="col-span-2 sm:col-span-1">
          {/* Opens the injuries newest first, so the one this count runs from is
              the row at the top. The figure is a distance, not a record. */}
          <Box value={h.days_since_last_incident} label="Days since the last medical incident"
               tone={tones.days_since} explain={thresholdWords('days_since', settings.data)}
               empty="No injuries recorded"
               to={drillTo(year, `injuries in ${year}, newest first`, { injured_only: 'true' })} />
        </div>
        <Box value={h.injuries} label={`Medical incidents in ${year}`} tone={tones.injuries}
             change={change('injuries')}
             explain={thresholdWords('injuries', settings.data)}
             to={drillTo(year, `injuries in ${year}`, { injured_only: 'true' })} />
        <Box value={h.first_aid} label="First aid injuries" tone={tones.first_aid}
             change={change('first_aid')}
             explain={thresholdWords('first_aid', settings.data)}
             to={drillTo(year, `first aid injuries in ${year}`, { injury_class: 'first_aid' })} />
        <Box value={h.medical} label="Medical aid injuries" tone={tones.medical_aid}
             change={change('medical_aid')}
             explain={thresholdWords('medical_aid', settings.data)}
             to={drillTo(year, `medical aid injuries in ${year}`, { injury_class: 'medical_aid' })} />
        <Box value={h.lost_time} label="Lost time injuries" tone={tones.lost_time}
             change={change('lost_time')}
             explain={thresholdWords('lost_time', settings.data)}
             to={drillTo(year, `lost time injuries in ${year}`, { injury_class: 'lost_time' })} />
      </div>

      {/* ── Injuries this year, as one bar: it is a composition ───────────── */}
      <div className="mb-3 rounded-xl border border-neutral-200 bg-white p-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Injuries this year</p>
        {h.injuries > 0 ? (
          <>
            <div className="mt-2 flex h-6 overflow-hidden rounded-md">
              {SEVERITY.map(({ key, cls, label }) => {
                const n = h[key === 'medical_aid' ? 'medical' : key] as number
                return n > 0 && (
                  <TabLink key={key}
                        to={drillTo(year, `${label.toLowerCase()} injuries in ${year}`, { injury_class: key })}
                        className={`${cls} flex items-center justify-center`}
                        style={{ width: `${(n / h.injuries) * 100}%` }}
                        title={`${label}: ${n} — open these`}>
                    <span className="text-[11px] font-bold text-white">{n}</span>
                  </TabLink>
                )
              })}
            </div>
            <Legend items={SEVERITY} />
          </>
        ) : (
          <p className="mt-2 text-sm text-neutral-500">No injuries recorded this year.</p>
        )}

        {/* Rates, hours and cost, after the injuries — and the rates moved here
            from Reports, worked out by the same function. */}
        <div className="mt-4 grid grid-cols-2 gap-3 border-t border-neutral-100 pt-4 sm:grid-cols-3 lg:grid-cols-5">
          <div title={thresholdWords('trir', settings.data) ?? undefined}>
            <Stat value={h.rir ?? '—'} label="Recordable incident rate"
                  sub={h.rir === null ? 'needs hours worked' : `${h.recordable} recordable`}
                  subTo={h.recordable > 0
                    ? drillTo(year, `the ${h.recordable} recordable incidents of ${year}`, { recordable_only: 'true' })
                    : undefined} />
          </div>
          <Stat value={h.lost_time_rate ?? '—'} label="Lost time rate"
                sub={h.lost_time_rate === null ? 'needs hours worked' : 'per 200,000 hours'} />
          <Stat value={h.dart_rate ?? '—'} label="DART rate"
                sub={h.dart_rate === null ? 'needs hours worked' : 'medical aid + lost time'} />
          <Stat value={h.lost_time_hours || '—'} label="Lost time hours" />
          <Stat value={h.cost_total ? `$${h.cost_total.toLocaleString()}` : '—'}
                label="Incident cost"
                // ★ Only what has been entered, and how much of the year that
                // is — a partial sum must not read as the whole cost.
                sub={`${h.cost_known_count} of ${h.incidents} incidents have a cost`} />
        </div>
        {tones.trir && (
          <p className={`mt-2 text-[11px] font-semibold ${TONE_STYLE[tones.trir].value}`}>
            Recordable rate: {TONE_STYLE[tones.trir].word}
          </p>
        )}
      </div>

      {(unowned.data?.count ?? 0) > 0 && (
        <TabLink to="/settings/areas"
              className="mb-3 flex flex-wrap items-center gap-2 rounded-lg bg-warning-50 px-3 py-2 transition-colors hover:bg-warning-100">
          <p className="min-w-0 flex-1 text-xs text-warning-800">
            <span className="font-bold tabular-nums">{unowned.data!.count}</span>{' '}
            {unowned.data!.count === 1 ? 'task belongs' : 'tasks belong'} to a part of
            the plant nobody is responsible for, so they are on your list rather than a
            supervisor's.
          </p>
          <span className="shrink-0 text-xs font-semibold text-warning-800 underline">
            Assign the areas
          </span>
        </TabLink>
      )}

      {h.rir_note && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg bg-warning-50 px-3 py-2">
          <p className="min-w-0 flex-1 text-xs text-warning-700">{h.rir_note}</p>
          <input value={hours} inputMode="numeric" aria-label="Hours worked this year"
                 onChange={(e) => setHours(e.target.value.replace(/[^0-9]/g, ''))}
                 placeholder="hours worked"
                 className="min-h-[36px] w-32 rounded-lg border border-warning-200 bg-white px-2.5 text-sm focus:outline-none" />
        </div>
      )}

      {settings.isError && (
        <p className="mb-2 text-[11px] text-neutral-500">
          The colour rules could not be loaded, so the boxes do not say what their colours mean.
        </p>
      )}

      <div className="mb-2 flex flex-wrap items-center justify-end gap-3">
        {isHse && (
          <TabLink to="/settings/dashboard"
                   className="min-h-[32px] rounded-md px-1.5 py-1.5 text-xs font-semibold text-neutral-500 hover:bg-neutral-50 hover:text-neutral-900">
            Colours and chart styles
          </TabLink>
        )}
        <button type="button" onClick={() => setChoosing(!choosing)}
                aria-expanded={choosing}
                className="min-h-[32px] rounded-md px-1.5 text-xs font-semibold text-neutral-500 hover:bg-neutral-50 hover:text-neutral-900">
          {choosing ? 'Done' : 'Choose what is on this page'}
        </button>
      </div>

      {choosing && (
        <div className="mb-3 rounded-xl border border-neutral-200 bg-white p-3.5">
          <p className="mb-2 max-w-[68ch] text-xs text-neutral-600">
            Yours only — everybody keeps their own. Use the arrows to put the
            one you look at first at the top.
          </p>
          <ul className="flex flex-col gap-1">
            {layout.data?.available.filter((p) => offered(p.key)).map((panel) => {
              const at = order.indexOf(panel.key)
              const on = at !== -1
              // ★ Saved against the server's own list, so a panel this person
              // cannot see (by supervisor) keeps whatever place it had.
              const full = layout.data!.panels
              const move = (from: number, to: number) => {
                const next = [...order]
                ;[next[from], next[to]] = [next[to], next[from]]
                saveLayout.mutate([...next, ...full.filter((k) => !offered(k))])
              }
              return (
                <li key={panel.key} className="flex items-center gap-2 text-sm">
                  <label className="flex min-h-[32px] min-w-0 flex-1 items-center gap-2 text-neutral-800">
                    <input type="checkbox" checked={on}
                           onChange={() => saveLayout.mutate(on
                             ? full.filter((k) => k !== panel.key)
                             : [...full, panel.key])} />
                    {panel.label}
                  </label>
                  {on && (
                    <>
                      <button type="button" aria-label={`Move ${panel.label} up`}
                              disabled={at === 0 || saveLayout.isPending}
                              onClick={() => move(at, at - 1)}
                              className="min-h-[32px] px-1.5 text-neutral-400 hover:text-neutral-800 disabled:opacity-30">
                        ↑
                      </button>
                      <button type="button" aria-label={`Move ${panel.label} down`}
                              disabled={at === order.length - 1 || saveLayout.isPending}
                              onClick={() => move(at, at + 1)}
                              className="min-h-[32px] px-1.5 text-neutral-400 hover:text-neutral-800 disabled:opacity-30">
                        ↓
                      </button>
                    </>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {order.map((key) => <Fragment key={key}>{panels[key]}</Fragment>)}
      </div>
    </div>
  )
}
