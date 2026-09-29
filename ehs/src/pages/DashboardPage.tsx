/**
 * The management dashboard.
 *
 * Built from the same figures as the spreadsheet HSE circulates, but not laid
 * out the same way. Their sheet gives nine tiles equal weight in one row, which
 * makes the reader do the ranking; this leads with the two things a management
 * meeting actually reacts to and puts the rest underneath.
 *
 * Three deliberate departures from the original:
 *
 *   **Days since the last medical incident is the largest number on the page.**
 *     It counts injuries only: counting everything meant reporting a good
 *     catch reset it to zero, which punishes the reporting it exists to
 *     encourage. It is
 *   the only figure here where a bigger number is better, and it is the one a
 *   plant puts on a board by the door. Everything else counts things going
 *   wrong.
 *
 *   **The injury count is one stacked bar, not four tiles.** 11 = 9 first aid +
 *   1 medical + 1 lost time is a composition, and four separate boxes make the
 *   reader add up and divide. Severity runs light to dark left to right, so the
 *   serious end of the bar is always on the same side.
 *
 *   **A missing figure says what is missing.** A zero injury rate reads like a
 *   good year rather than like a missing divisor, and on a dashboard that goes
 *   to management that mistake is expensive.
 *
 * Charts are divs. All of them are bars, a charting library would be the
 * largest dependency in the app, and these print.
 */
import { Fragment, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { CalendarClock, TrendingUp } from 'lucide-react'
import { api } from '@/lib/api'
import { formatAreaPath } from '@/lib/areaPath'
import {
  PageHeader, SectionTitle,
} from '@/components/ui'
import { formatDate } from '@/lib/formatDate'

interface Headline {
  year: number; injuries: number; first_aid: number; medical: number
  lost_time: number; recordable: number; rir: number | null
  days_since_last_incident: number | null
  cost_total: number; cost_average: number | null; cost_known_count: number
  lost_time_hours: number; rir_note: string | null
}
interface MonthRow { month: string; first_aid: number; medical_aid: number; lost_time: number }
interface NearMissRow { month: string; this_year: number; last_year: number }
interface CountRow { label: string; count: number; id?: string | null }
interface ParticipationRow {
  person: string; toolbox_talk: number; good_catch: number; hazard_assessment: number
}
interface Dashboard {
  headline: Headline
  medical_events: MonthRow[]
  near_misses: NearMissRow[]
  body_parts: CountRow[]
  injured_departments: CountRow[]
  injured_locations: CountRow[]
  participation: ParticipationRow[]
  jhsc: {
    available: boolean
    reason: string | null
    months?: { id: string; month: string; held_on: string | null; present: number; invited: number }[]
    members?: number
  }
}

const MONTHS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D']
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
                     'July', 'August', 'September', 'October', 'November', 'December']

/** Severity light to dark, so the serious end of every bar is the same colour
 *  and the same side throughout the page. */
const SEVERITY = [
  { key: 'first_aid', label: 'First aid', cls: 'bg-primary-300' },
  { key: 'medical_aid', label: 'Medical aid', cls: 'bg-warning-400' },
  { key: 'lost_time', label: 'Lost time', cls: 'bg-danger-500' },
] as const

/**
 * A link from a figure to the records it was counted from.
 *
 * ★ The parameters are the server's own, and they say the same thing the
 * figure's own query says — same year boundary, same definition of
 * "recordable", same null department. A drill-down that lands on a list
 * filtered by something merely *near* the number is worse than none: the
 * reader counts the rows, gets a different answer, and stops trusting both.
 *
 * `via` is what the list says it is showing. It travels in the URL so the page
 * showing it does not have to reconstruct a description from the filters.
 */
function drillTo(year: number, via: string, params: Record<string, string | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) q.append(k, v)
  q.set('occurred_from', `${year}-01-01`)
  q.set('occurred_to', `${year}-12-31`)
  q.set('via', via)
  return `/incidents?${q.toString()}`
}

/**
 * The same, for one month of a year.
 *
 * ★ The month's last day is computed as day 0 of the next one, which gets
 * February right without a table — and the range is written in local calendar
 * days, which is how the server reads it. The charts group months in Ontario
 * time, so anything else would put the last evening of a month in the wrong
 * bar when it was opened.
 */
function monthDrill(year: number, monthIndex: number, via: string,
                    params: Record<string, string | undefined>): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  const last = new Date(year, monthIndex + 1, 0).getDate()
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) q.append(k, v)
  q.set('occurred_from', `${year}-${pad(monthIndex + 1)}-01`)
  q.set('occurred_to', `${year}-${pad(monthIndex + 1)}-${pad(last)}`)
  q.set('via', `${via} ${year}`)
  return `/incidents?${q.toString()}`
}

/** A figure that can be opened. Anything not worth opening stays plain text. */
function DrillLink({ to, children, className = '' }: {
  to: string; children: React.ReactNode; className?: string
}) {
  return (
    <Link to={to}
          className={`rounded-sm underline decoration-transparent underline-offset-2 transition-colors hover:decoration-current focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500 ${className}`}>
      {children}
    </Link>
  )
}

function Panel({ title, children, note, className = '' }: {
  title: string; children: React.ReactNode; note?: string; className?: string
}) {
  return (
    <section className={`rounded-xl border border-neutral-200 bg-white p-4 ${className}`}>
      <SectionTitle className="mb-3">{title}</SectionTitle>
      {children}
      {note && <p className="mt-2.5 text-[11px] leading-relaxed text-neutral-400">{note}</p>}
    </section>
  )
}

function Stat({ value, label, sub, to, subTo }: {
  value: string | number; label: string; sub?: string
  /** Where the figure's own records are, when it has some. */
  to?: string
  /** Where the smaller figure underneath leads, when that is the countable
   *  one — the recordable rate is a rate, but "12 recordable" is a list. */
  subTo?: string
}) {
  return (
    <div>
      <p className="text-xl font-bold tabular-nums leading-none text-neutral-900">
        {to ? <DrillLink to={to}>{value}</DrillLink> : value}
      </p>
      <p className="mt-1 text-[11px] font-medium uppercase tracking-wide text-neutral-500">
        {label}
      </p>
      {sub && (
        <p className="mt-0.5 text-[11px] text-neutral-400">
          {subTo ? <DrillLink to={subTo}>{sub}</DrillLink> : sub}
        </p>
      )}
    </div>
  )
}

function Legend({ items }: { items: readonly { label: string; cls: string }[] }) {
  return (
    <ul className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5 text-[11px] text-neutral-600">
          <span className={`h-2 w-2 rounded-sm ${i.cls}`} aria-hidden />{i.label}
        </li>
      ))}
    </ul>
  )
}

function EmptyNote({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-neutral-200 px-3 py-6 text-center text-sm text-neutral-500">
      {children}
    </p>
  )
}

export default function DashboardPage() {
  const [hours, setHours] = useState('')
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard', hours],
    queryFn: () => api.get<Dashboard>(
      `/api/v1/dashboard${hours ? `?hours_worked=${hours}` : ''}`),
  })

  // Which panels this person wants, and in what order. HSE asked for the
  // dashboard to be configurable and chose "pick from the library and arrange
  // it" when the PRD asked how — this is that, kept to what it says. The
  // library lives on the server so a panel added later appears in everybody's
  // chooser without handing each of them a new frontend.
  const queryClient = useQueryClient()
  const layout = useQuery({
    queryKey: ['dashboard-layout'],
    queryFn: () => api.get<{ panels: string[]; available: { key: string; label: string }[] }>(
      '/api/v1/dashboard/layout'),
  })
  // ★ How much area work nobody has been named for. On the dashboard because
  // that is the screen HSE has open — a number that only appears on the page
  // where you would go to fix it is a number nobody is prompted by.
  const unowned = useQuery({
    queryKey: ['unattributed'],
    queryFn: () => api.get<{ count: number; examples: string[] }>(
      '/api/v1/actions/unattributed'),
  })
  const [choosing, setChoosing] = useState(false)
  const order = layout.data?.panels ?? []
  const saveLayout = useMutation({
    mutationFn: (panels: string[]) =>
      api.put('/api/v1/dashboard/layout', { panels }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard-layout'] }),
  })

  // ★ Every hook above the two early returns below. These were underneath
  // them: while the figures were loading the component ran six hooks, and the
  // render after they arrived ran ten — which is the one thing React cannot
  // recover from, and it took the whole tab down rather than the panel.
  if (isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  if (!data) return null

  const h = data.headline
  const monthMax = Math.max(1, ...data.medical_events.map(
    (r) => r.first_aid + r.medical_aid + r.lost_time))
  const nearMax = Math.max(1, ...data.near_misses.flatMap((r) => [r.this_year, r.last_year]))
  const nearThis = data.near_misses.reduce((n, r) => n + r.this_year, 0)
  const nearLast = data.near_misses.reduce((n, r) => n + r.last_year, 0)
  const partMax = Math.max(1, ...data.participation.map(
    (p) => p.toolbox_talk + p.good_catch + p.hazard_assessment))

  const panels: Record<string, React.ReactNode> = {
    injuries_by_month: (
<Panel title="Injuries by month" className="lg:col-span-2">
          {/* items-stretch, not items-end. The bars are sized with percentage
              heights, and a percentage resolves against a parent with a
              definite height — which `items-end` denies it, leaving every
              column auto-height and every bar computing to zero. The chart was
              not empty; it was invisible. */}
          <div className="flex h-32 items-stretch gap-1.5">
            {data.medical_events.map((r, i) => {
              const total = r.first_aid + r.medical_aid + r.lost_time
              return (
                <Link key={r.month}
                      to={monthDrill(data.headline.year, i, `injuries in ${MONTH_NAMES[i]}`,
                                     { injured_only: 'true' })}
                      className="flex flex-1 flex-col items-center gap-1.5 rounded-sm hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500"
                      title={`${MONTH_NAMES[i]}: ${total} — open these`}>
                  <div className="flex w-full flex-1 flex-col justify-end">
                    <div className="flex w-full flex-col-reverse"
                         style={{ height: `${(total / monthMax) * 100}%` }}>
                      {SEVERITY.map(({ key, cls }) => r[key] > 0 && (
                        <div key={key} className={cls}
                             style={{ height: `${(r[key] / Math.max(total, 1)) * 100}%` }} />
                      ))}
                    </div>
                  </div>
                  <span className="text-[11px] text-neutral-400">{MONTHS[i]}</span>
                </Link>
              )
            })}
          </div>
          <Legend items={SEVERITY} />
        </Panel>
    ),
    near_misses: (
<Panel title="Near misses and good catches"
               note="A rising line here is good — it means people are reporting what nearly happened.">
          <div className="flex h-28 items-stretch gap-1.5">
            {data.near_misses.map((r, i) => (
              <Link key={r.month}
                    to={monthDrill(data.headline.year, i,
                                   `near misses in ${MONTH_NAMES[i]}`,
                                   { form_kind: 'near_miss' })}
                    className="flex flex-1 flex-col items-center gap-1.5 rounded-sm hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500"
                    title={`${MONTH_NAMES[i]}: ${r.this_year} (last year ${r.last_year}) — open this year's`}>
                <div className="relative flex w-full flex-1 items-end">
                  <div className="absolute inset-x-0 bottom-0 rounded-sm bg-neutral-200"
                       style={{ height: `${(r.last_year / nearMax) * 100}%` }} />
                  <div className="relative w-full rounded-sm bg-primary-500"
                       style={{ height: `${(r.this_year / nearMax) * 100}%` }} />
                </div>
                <span className="text-[11px] text-neutral-400">{MONTHS[i]}</span>
              </Link>
            ))}
          </div>
          <Legend items={[{ label: 'This year', cls: 'bg-primary-500' },
                          { label: 'Last year', cls: 'bg-neutral-200' }]} />
        </Panel>
    ),
    participation: (
<Panel title="Safety participation"
               note="Toolbox talks delivered, good catches reported, risk assessments completed.">
          {data.participation.length === 0 ? (
            <EmptyNote>Nothing recorded yet this year.</EmptyNote>
          ) : (
            <ul className="flex flex-col gap-2">
              {data.participation.map((p) => {
                const total = p.toolbox_talk + p.good_catch + p.hazard_assessment
                return (
                  <li key={p.person} className="flex items-center gap-2">
                    <span className="w-24 shrink-0 truncate text-right text-xs text-neutral-600">
                      {p.person}
                    </span>
                    <span className="flex h-4 flex-1 overflow-hidden rounded-sm bg-neutral-100">
                      {([['toolbox_talk', 'bg-primary-500', 'Toolbox talks'],
                         ['good_catch', 'bg-warning-400', 'Good catches'],
                         ['hazard_assessment', 'bg-neutral-400', 'Risk assessments']] as const)
                        .map(([k, cls, label]) => p[k] > 0 && (
                          <span key={k} className={cls}
                                style={{ width: `${(p[k] / partMax) * 100}%` }}
                                title={`${label}: ${p[k]}`} />
                        ))}
                    </span>
                    <span className="w-5 text-xs font-semibold tabular-nums text-neutral-800">
                      {total}
                    </span>
                  </li>
                )
              })}
              <li>
                <Legend items={[{ label: 'Toolbox talks', cls: 'bg-primary-500' },
                                { label: 'Good catches', cls: 'bg-warning-400' },
                                { label: 'Risk assessments', cls: 'bg-neutral-400' }]} />
              </li>
            </ul>
          )}
        </Panel>
    ),
    injured_locations: (
      /* ★ The plant area the reporter chose on the form. The panel headed
         "Where people are getting hurt" was grouped by department — and an
         incident's department is the department of the person hurt, so somebody
         from Production injured in the warehouse counted towards Production and
         the area they recorded appeared on the dashboard nowhere. Round 4
         reported it as the location being inaccurate; it was not the location
         at all. Both cuts are useful and the one that answers "where" is this. */
      <Panel title="Where injuries are happening">
          {data.injured_locations.length === 0 ? (
            <EmptyNote>No injuries recorded this year.</EmptyNote>
          ) : (
            <Bars
              rows={data.injured_locations.map((r) => ({
                ...r, label: formatAreaPath(r.label) ?? r.label,
              }))}
              linkFor={(r) => drillTo(
                h.year, `injuries in ${r.label}`,
                r.id
                  ? { injured_only: 'true', location_id: r.id }
                  : { injured_only: 'true' })}
            />
          )}
        </Panel>
    ),
    injured_departments: (
<Panel title="Injured by department">
          {data.injured_departments.length === 0 ? (
            <EmptyNote>No injuries recorded this year.</EmptyNote>
          ) : (
            <Bars
              rows={data.injured_departments}
              linkFor={(r) => drillTo(
                h.year, `injuries in ${r.label}`,
                // The id decides, not the label: "Not recorded" is this
                // chart's name for a null department, and a row without an id
                // has to ask for exactly that rather than for a department
                // that happens to be called "Not recorded".
                r.id
                  ? { injured_only: 'true', department_id: r.id }
                  : { injured_only: 'true', no_department: 'true' })}
            />
          )}
        </Panel>
    ),
    body_parts: (
<Panel title="Part of the body affected">
          {data.body_parts.length === 0 ? (
            <EmptyNote>
              No body parts recorded. These are entered in Section B of an injury report.
            </EmptyNote>
          ) : (
            <Bars
              rows={data.body_parts}
              linkFor={(r) => drillTo(h.year, `injuries to the ${r.label.toLowerCase()}`,
                                      { body_part_label: r.label })}
            />
          )}
        </Panel>
    ),
    jhsc_attendance: (
<Panel title="JHSC meeting attendance" className="lg:col-span-2"
               note="OHSA s.9 wants the committee meeting and a record of who was there.">
          {/* The months were always in the response and only the "nothing to
              show" branch was ever written, so a committee that had met twice
              still read as having no data. */}
          {data.jhsc.available && (data.jhsc.months?.length ?? 0) > 0 ? (
            <ul className="flex flex-col gap-2">
              {data.jhsc.months!.map((m) => {
                const invited = Math.max(m.invited, 1)
                const pct = Math.round((m.present / invited) * 100)
                return (
                  // ★ The meeting, not the month. A committee that met twice
                  // in September gave two rows the same key, and React drew one
                  // of them — so the second meeting's attendance was missing
                  // from the page with nothing to say so.
                  <li key={m.id} className="flex items-center gap-2">
                    <span className="w-24 shrink-0 text-right text-xs text-neutral-600">
                      {MONTH_NAMES[Number(m.month) - 1] ?? m.month}
                      {m.held_on && (
                        <span className="block text-[11px] text-neutral-400">
                          {formatDate(m.held_on)}
                        </span>
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


  return (
    <div className="p-4">
      <header className="mb-4 flex flex-wrap items-baseline gap-x-3">
      <PageHeader title="HSE dashboard" />
        <span className="text-sm text-neutral-500">{h.year}</span>
      </header>

      {/* ── The two things a management meeting reacts to ─────────────────── */}
      <div className="mb-3 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,320px)_1fr]">
        <div className="flex items-center gap-4 rounded-xl border border-primary-200 bg-primary-50 p-5">
          <CalendarClock className="h-8 w-8 shrink-0 text-primary-600" aria-hidden />
          <div>
            <p className="text-5xl font-bold tabular-nums leading-none text-primary-800">
              {h.days_since_last_incident !== null ? (
                // Opens the injuries newest first, so the one this count runs
                // from is the row at the top. There is no "the last injury"
                // link to give: the figure is a distance, not a record.
                <DrillLink to={drillTo(h.year, `injuries in ${h.year}, newest first`,
                                       { injured_only: 'true' })}>
                  {h.days_since_last_incident}
                </DrillLink>
              ) : '—'}
            </p>
            <p className="mt-1.5 text-xs font-semibold uppercase tracking-wide text-primary-700">
              Days since the last medical incident
            </p>
          </div>
        </div>

        <div className="rounded-xl border border-neutral-200 bg-white p-5">
          <div className="flex items-baseline gap-2.5">
            <DrillLink to={drillTo(h.year, `injuries in ${h.year}`, { injured_only: 'true' })}
                       className="text-4xl font-bold tabular-nums leading-none text-neutral-900">
              {h.injuries}
            </DrillLink>
            <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
              injuries this year
            </span>
          </div>

          {/* One bar rather than four tiles: this is a composition. */}
          {h.injuries > 0 ? (
            <>
              <div className="mt-3 flex h-6 overflow-hidden rounded-md">
                {SEVERITY.map(({ key, cls, label }) => {
                  const n = h[key === 'medical_aid' ? 'medical' : key] as number
                  return n > 0 && (
                    <Link key={key}
                          to={drillTo(h.year, `${label.toLowerCase()} injuries in ${h.year}`,
                                      { injury_class: key })}
                          className={`${cls} flex items-center justify-center`}
                          style={{ width: `${(n / h.injuries) * 100}%` }}
                          title={`${label}: ${n} — open these`}>
                      <span className="text-[11px] font-bold text-white">{n}</span>
                    </Link>
                  )
                })}
              </div>
              <Legend items={SEVERITY} />
            </>
          ) : (
            <p className="mt-3 text-sm text-neutral-500">No injuries recorded this year.</p>
          )}
        </div>
      </div>

      {/* ── Everything else, at the weight it deserves ────────────────────── */}
      <div className="mb-3 grid grid-cols-2 gap-3 rounded-xl border border-neutral-200 bg-white p-4 sm:grid-cols-4">
        <Stat value={h.rir ?? '—'} label="Recordable incident rate"
              sub={h.rir === null ? 'needs hours worked' : `${h.recordable} recordable`}
              subTo={h.recordable > 0
                ? drillTo(h.year, `the ${h.recordable} recordable incidents of ${h.year}`,
                          { recordable_only: 'true' })
                : undefined} />
        <Stat value={h.lost_time_hours || '—'} label="Lost time hours" />
        <Stat value={h.cost_total ? `$${h.cost_total.toLocaleString()}` : '—'}
              label="Incident cost"
              sub={h.cost_average !== null
                ? `avg $${h.cost_average} of ${h.cost_known_count} costed`
                : 'none costed yet'} />
        <Stat value={nearThis} label="Near misses reported"
              sub={nearLast > 0 ? `${nearLast} last year` : undefined}
              to={nearThis > 0
                ? drillTo(h.year, `near misses reported in ${h.year}`,
                          { form_kind: 'near_miss' })
                : undefined} />
      </div>

      {(unowned.data?.count ?? 0) > 0 && (
        <Link to="/settings/area-owners"
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
        </Link>
      )}

      {h.rir_note && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg bg-warning-50 px-3 py-2">
          <p className="min-w-0 flex-1 text-xs text-warning-700">{h.rir_note}</p>
          <input value={hours} inputMode="numeric"
                 onChange={(e) => setHours(e.target.value.replace(/[^0-9]/g, ''))}
                 placeholder="hours worked"
                 className="min-h-[36px] w-32 rounded-lg border border-warning-200 bg-white px-2.5 text-sm focus:outline-none" />
        </div>
      )}

      <div className="mb-2 flex justify-end">
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
            {layout.data?.available.map((panel) => {
              const at = order.indexOf(panel.key)
              const on = at !== -1
              return (
                <li key={panel.key} className="flex items-center gap-2 text-sm">
                  <label className="flex min-h-[32px] min-w-0 flex-1 items-center gap-2 text-neutral-800">
                    <input type="checkbox" checked={on}
                           onChange={() => saveLayout.mutate(on
                             ? order.filter((k) => k !== panel.key)
                             : [...order, panel.key])} />
                    {panel.label}
                  </label>
                  {on && (
                    <>
                      <button type="button" aria-label={`Move ${panel.label} up`}
                              disabled={at === 0 || saveLayout.isPending}
                              onClick={() => {
                                const next = [...order]
                                ;[next[at - 1], next[at]] = [next[at], next[at - 1]]
                                saveLayout.mutate(next)
                              }}
                              className="min-h-[32px] px-1.5 text-neutral-400 hover:text-neutral-800 disabled:opacity-30">
                        ↑
                      </button>
                      <button type="button" aria-label={`Move ${panel.label} down`}
                              disabled={at === order.length - 1 || saveLayout.isPending}
                              onClick={() => {
                                const next = [...order]
                                ;[next[at], next[at + 1]] = [next[at + 1], next[at]]
                                saveLayout.mutate(next)
                              }}
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

/** Ranked horizontal bars — the shape of the answer to "which is worst". */
function Bars({ rows, linkFor }: {
  rows: CountRow[]
  /** Where this row's records are, when they can be reached. */
  linkFor?: (row: CountRow) => string
}) {
  const max = Math.max(1, ...rows.map((r) => r.count))
  return (
    <ul className="flex flex-col gap-1.5">
      {rows.map((r) => {
        const bar = (
          <>
            <span className="w-28 shrink-0 truncate text-right text-xs text-neutral-600"
                  title={r.label}>
              {r.label}
            </span>
            <span className="h-4 shrink-0 rounded-sm bg-primary-500"
                  style={{ width: `${Math.max(3, (r.count / max) * 62)}%` }} />
            <span className="text-xs font-semibold tabular-nums text-neutral-800">{r.count}</span>
          </>
        )
        return (
          <li key={r.label}>
            {linkFor ? (
              <Link to={linkFor(r)}
                    className="flex items-center gap-2 rounded-sm py-0.5 hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500"
                    title={`${r.label}: ${r.count} — open these`}>
                {bar}
              </Link>
            ) : (
              <span className="flex items-center gap-2 py-0.5">{bar}</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}
