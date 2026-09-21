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
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CalendarClock, TrendingUp } from 'lucide-react'
import { api } from '@/lib/api'
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
interface CountRow { label: string; count: number }
interface ParticipationRow {
  person: string; toolbox_talk: number; good_catch: number; hazard_assessment: number
}
interface Dashboard {
  headline: Headline
  medical_events: MonthRow[]
  near_misses: NearMissRow[]
  body_parts: CountRow[]
  injured_departments: CountRow[]
  participation: ParticipationRow[]
  jhsc: {
    available: boolean
    reason: string | null
    months?: { month: string; held_on: string | null; present: number; invited: number }[]
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

function Panel({ title, children, note, className = '' }: {
  title: string; children: React.ReactNode; note?: string; className?: string
}) {
  return (
    <section className={`rounded-xl border border-neutral-200 bg-white p-4 ${className}`}>
      <h2 className="mb-3 text-xs font-bold uppercase tracking-wider text-neutral-400">
        {title}
      </h2>
      {children}
      {note && <p className="mt-2.5 text-[11px] leading-relaxed text-neutral-400">{note}</p>}
    </section>
  )
}

function Stat({ value, label, sub }: {
  value: string | number; label: string; sub?: string
}) {
  return (
    <div>
      <p className="text-xl font-bold tabular-nums leading-none text-neutral-900">{value}</p>
      <p className="mt-1 text-[11px] font-medium uppercase tracking-wide text-neutral-500">
        {label}
      </p>
      {sub && <p className="mt-0.5 text-[11px] text-neutral-400">{sub}</p>}
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

  return (
    <div className="p-4">
      <header className="mb-4 flex flex-wrap items-baseline gap-x-3">
        <h1 className="text-lg font-bold tracking-tight text-neutral-900">
          HSE dashboard
        </h1>
        <span className="text-sm text-neutral-500">{h.year}</span>
      </header>

      {/* ── The two things a management meeting reacts to ─────────────────── */}
      <div className="mb-3 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,320px)_1fr]">
        <div className="flex items-center gap-4 rounded-xl border border-primary-200 bg-primary-50 p-5">
          <CalendarClock className="h-8 w-8 shrink-0 text-primary-600" aria-hidden />
          <div>
            <p className="text-5xl font-bold tabular-nums leading-none text-primary-800">
              {h.days_since_last_incident ?? '—'}
            </p>
            <p className="mt-1.5 text-xs font-semibold uppercase tracking-wide text-primary-700">
              Days since the last medical incident
            </p>
          </div>
        </div>

        <div className="rounded-xl border border-neutral-200 bg-white p-5">
          <div className="flex items-baseline gap-2.5">
            <span className="text-4xl font-bold tabular-nums leading-none text-neutral-900">
              {h.injuries}
            </span>
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
                    <div key={key} className={`${cls} flex items-center justify-center`}
                         style={{ width: `${(n / h.injuries) * 100}%` }}
                         title={`${label}: ${n}`}>
                      <span className="text-[11px] font-bold text-white">{n}</span>
                    </div>
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
              sub={h.rir === null ? 'needs hours worked' : `${h.recordable} recordable`} />
        <Stat value={h.lost_time_hours || '—'} label="Lost time hours" />
        <Stat value={h.cost_total ? `$${h.cost_total.toLocaleString()}` : '—'}
              label="Incident cost"
              sub={h.cost_average !== null
                ? `avg $${h.cost_average} of ${h.cost_known_count} costed`
                : 'none costed yet'} />
        <Stat value={nearThis} label="Near misses reported"
              sub={nearLast > 0 ? `${nearLast} last year` : undefined} />
      </div>

      {h.rir_note && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg bg-warning-50 px-3 py-2">
          <p className="min-w-0 flex-1 text-xs text-warning-700">{h.rir_note}</p>
          <input value={hours} inputMode="numeric"
                 onChange={(e) => setHours(e.target.value.replace(/[^0-9]/g, ''))}
                 placeholder="hours worked"
                 className="min-h-[36px] w-32 rounded-lg border border-warning-200 bg-white px-2.5 text-sm focus:outline-none" />
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {/* ── Trend ──────────────────────────────────────────────────────── */}
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
                <div key={r.month} className="flex flex-1 flex-col items-center gap-1.5">
                  <div className="flex w-full flex-1 flex-col justify-end"
                       title={`${MONTH_NAMES[i]}: ${total}`}>
                    <div className="flex w-full flex-col-reverse"
                         style={{ height: `${(total / monthMax) * 100}%` }}>
                      {SEVERITY.map(({ key, cls }) => r[key] > 0 && (
                        <div key={key} className={cls}
                             style={{ height: `${(r[key] / Math.max(total, 1)) * 100}%` }} />
                      ))}
                    </div>
                  </div>
                  <span className="text-[10px] text-neutral-400">{MONTHS[i]}</span>
                </div>
              )
            })}
          </div>
          <Legend items={SEVERITY} />
        </Panel>

        {/* ── Near misses ────────────────────────────────────────────────── */}
        <Panel title="Near misses and good catches"
               note="A rising line here is good — it means people are reporting what nearly happened.">
          <div className="flex h-28 items-stretch gap-1.5">
            {data.near_misses.map((r, i) => (
              <div key={r.month} className="flex flex-1 flex-col items-center gap-1.5">
                <div className="relative flex w-full flex-1 items-end"
                     title={`${MONTH_NAMES[i]}: ${r.this_year} (last year ${r.last_year})`}>
                  <div className="absolute inset-x-0 bottom-0 rounded-sm bg-neutral-200"
                       style={{ height: `${(r.last_year / nearMax) * 100}%` }} />
                  <div className="relative w-full rounded-sm bg-primary-500"
                       style={{ height: `${(r.this_year / nearMax) * 100}%` }} />
                </div>
                <span className="text-[10px] text-neutral-400">{MONTHS[i]}</span>
              </div>
            ))}
          </div>
          <Legend items={[{ label: 'This year', cls: 'bg-primary-500' },
                          { label: 'Last year', cls: 'bg-neutral-200' }]} />
        </Panel>

        {/* ── Participation ──────────────────────────────────────────────── */}
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

        {/* ── Where and what ─────────────────────────────────────────────── */}
        <Panel title="Where people are getting hurt">
          {data.injured_departments.length === 0 ? (
            <EmptyNote>No injuries recorded this year.</EmptyNote>
          ) : (
            <Bars rows={data.injured_departments} />
          )}
        </Panel>

        <Panel title="Part of the body affected">
          {data.body_parts.length === 0 ? (
            <EmptyNote>
              No body parts recorded. These are entered in Section B of an injury report.
            </EmptyNote>
          ) : (
            <Bars rows={data.body_parts} />
          )}
        </Panel>

        {/* ── The gap, stated ────────────────────────────────────────────── */}
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
                  <li key={m.month} className="flex items-center gap-2">
                    <span className="w-24 shrink-0 text-right text-xs text-neutral-600">
                      {MONTH_NAMES[Number(m.month) - 1] ?? m.month}
                      {m.held_on && (
                        <span className="block text-[10px] text-neutral-400">
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
      </div>
    </div>
  )
}

/** Ranked horizontal bars — the shape of the answer to "which is worst". */
function Bars({ rows }: { rows: CountRow[] }) {
  const max = Math.max(1, ...rows.map((r) => r.count))
  return (
    <ul className="flex flex-col gap-1.5">
      {rows.map((r) => (
        <li key={r.label} className="flex items-center gap-2">
          <span className="w-28 shrink-0 truncate text-right text-xs text-neutral-600"
                title={r.label}>
            {r.label}
          </span>
          <span className="h-4 shrink-0 rounded-sm bg-primary-500"
                style={{ width: `${Math.max(3, (r.count / max) * 62)}%` }} />
          <span className="text-xs font-semibold tabular-nums text-neutral-800">{r.count}</span>
        </li>
      ))}
    </ul>
  )
}
