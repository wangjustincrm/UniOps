/**
 * ★ UAT 14: the Food Safety dashboard, beside the HSE one.
 *
 * Kirk's list, in his order: the food safety near misses recorded this year;
 * this month's, with the change on last month as a percentage and an arrow;
 * the risk ratings (Low, Medium, High, Critical); the investigations open,
 * pending and closed; only the food safety corrective actions — created,
 * completed and outstanding; the near misses month by month; and the average
 * days to resolve them, worked out as for every incident. Every figure opens
 * the records it counted.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import TabLink from '@/components/TabLink'
import { SectionTitle } from '@/components/ui'
import { MONTH_NAMES, MonthColumns } from '@/components/dashboard/charts'
import { DashboardTitles, MonthChange } from '@/components/dashboard/MonthChange'
import { BandBadge, useFoodSafetyScales } from '@/components/FoodSafetyRisk'

interface FoodSafety {
  year: number; total: number
  month: { month: string; count: number; previous: string; previous_count: number; change_percent: number | null }
  bands: Record<string, number>; not_assessed: number
  investigations: { open: number; under_investigation: number; closed: number }
  capa: { created: number; completed: number; outstanding: number }
  by_month: { month: string; count: number }[]
  average_days: number | null; resolved: number
}

const pad = (n: number) => String(n).padStart(2, '0')
/** Every status but closed (and draft, which the register's food-safety filter leaves out). */
const NOT_CLOSED = ['submitted', 'under_investigation', 'pending_closure']

/** The register, narrowed to food safety near misses and a period — the same
 *  rule and the same period the figure was counted with. */
function incidents(via: string, from: string, to: string, params: Record<string, string | string[]> = {}): string {
  const q = new URLSearchParams({ food_safety: 'true' })
  for (const [k, v] of Object.entries(params)) for (const one of [v].flat()) q.append(k, one)
  q.set('occurred_from', from)
  q.set('occurred_to', to)
  q.set('via', via)
  return `/incidents?${q.toString()}`
}
function monthRange(ym: string): [string, string] {
  const [y, m] = ym.split('-').map(Number)
  return [`${y}-${pad(m)}-01`, `${y}-${pad(m)}-${pad(new Date(y, m, 0).getDate())}`]
}

const tileCls = 'flex min-h-[92px] flex-col justify-between rounded-xl border border-neutral-200 bg-white p-4 hover:border-primary-300'

function Tile({ value, label, to, aside, sub }: {
  value: React.ReactNode; label: string; to?: string; aside?: React.ReactNode; sub?: React.ReactNode
}) {
  const body = (
    <>
      <span className="flex items-start justify-between gap-2">
        <span className="text-3xl font-bold tabular-nums leading-none text-neutral-900">{value}</span>
        {aside}
      </span>
      <span className="mt-2 text-[11px] font-semibold uppercase tracking-wide text-neutral-600">{label}</span>
      {sub && <span className="mt-0.5 text-[11px] text-neutral-500">{sub}</span>}
    </>
  )
  return to
    ? <TabLink to={to} className={tileCls} title={`${label} — open these`}>{body}</TabLink>
    : <div className={tileCls.replace(' hover:border-primary-300', '')}>{body}</div>
}

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-neutral-200 bg-neutral-50/60 p-4">
      <SectionTitle className="mb-3">{title}</SectionTitle>
      {children}
      {note && <p className="mt-2.5 text-[11px] leading-relaxed text-neutral-500">{note}</p>}
    </section>
  )
}

export default function FoodSafetyDashboardPage() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['dashboard', 'food-safety'],
    queryFn: () => api.get<FoodSafety>('/api/v1/dashboard/food-safety'),
  })
  const scales = useFoodSafetyScales()
  const bands = scales.data?.bands ?? []

  if (isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  if (isError || !data) {
    return (
      <div className="p-4">
        <div className="mb-4"><DashboardTitles current="food" /></div>
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2.5">
          <p className="min-w-0 flex-1 text-sm text-danger-800">Could not load the Food Safety dashboard.</p>
          <button type="button" onClick={() => refetch()} disabled={isFetching}
                  className="min-h-[36px] rounded-md bg-white px-3 text-sm font-semibold text-danger-800 ring-1 ring-danger-300 hover:bg-danger-100 disabled:opacity-50">
            {isFetching ? 'Trying…' : 'Try again'}
          </button>
        </div>
      </div>
    )
  }

  const y = data.year
  const yearFrom = `${y}-01-01`
  const yearTo = `${y}-12-31`
  const [mFrom, mTo] = monthRange(data.month.month)
  const monthName = MONTH_NAMES[Number(data.month.month.slice(5)) - 1]
  const order = ['critical', 'high', 'medium', 'low']

  return (
    <div className="p-4">
      <header className="mb-4 flex flex-wrap items-baseline gap-x-3">
        <DashboardTitles current="food" />
        <span className="text-sm text-neutral-500">{y}</span>
      </header>

      <div className="mb-3 grid gap-3 sm:grid-cols-3">
        <Tile value={data.total} label={`Food safety near misses in ${y}`}
              to={incidents(`food safety near misses in ${y}`, yearFrom, yearTo)} />
        <Tile value={data.month.count} label={`Food safety near misses in ${monthName}`}
              to={incidents(`food safety near misses in ${monthName} ${y}`, mFrom, mTo)}
              aside={<MonthChange now={data.month.count} before={data.month.previous_count}
                                  month={data.month.month} previous={data.month.previous}
                                  percent={data.month.change_percent} size="tile" upIsGood />}
              sub={`${data.month.previous_count} last month`} />
        <Tile value={data.average_days ?? '—'} label="Average days to resolve"
              to={incidents(`food safety near misses closed in ${y}`, yearFrom, yearTo, { status: 'closed' })}
              sub={`Over the ${data.resolved} closed; from the report being sent to its close.`} />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Section title="Risk rating"
                 note={`By each near miss's latest Food Safety Risk Assessment. ${data.not_assessed} not assessed yet. This month's figures are the month so far.`}>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {order.map((code) => (
              <TabLink key={code} to={`/food-safety?band=${code}&occurred_from=${yearFrom}&occurred_to=${yearTo}`}
                       className={tileCls}
                       title="Open these in the Food Safety Risk Register">
                <span className="text-3xl font-bold tabular-nums leading-none text-neutral-900">{data.bands[code] ?? 0}</span>
                <span className="mt-2"><BandBadge band={code} bands={bands} /></span>
              </TabLink>
            ))}
          </div>
        </Section>

        <Section title="Investigations"
                 note="Open: reported, the investigation not yet signed. Under investigation: signed, and not closed while its corrective actions are completed and verified. Closed: closed.">
          <div className="grid grid-cols-3 gap-2">
            <Tile value={data.investigations.open} label="Open"
                  to={incidents(`food safety near misses open in ${y}`, yearFrom, yearTo,
                                { status: NOT_CLOSED, investigation_signed: 'false' })} />
            <Tile value={data.investigations.under_investigation} label="Under investigation"
                  to={incidents(`food safety near misses under investigation in ${y}`, yearFrom, yearTo,
                                { status: NOT_CLOSED, investigation_signed: 'true' })} />
            <Tile value={data.investigations.closed} label="Closed"
                  to={incidents(`food safety investigations closed in ${y}`, yearFrom, yearTo, { status: 'closed' })} />
          </div>
        </Section>

        <Section title="Corrective actions"
                 note="Only those raised on a food safety near miss. Each opens the near misses they were raised on.">
          <div className="grid grid-cols-3 gap-2">
            <Tile value={data.capa.created} label="Created"
                  to={incidents(`food safety near misses with a corrective action in ${y}`, yearFrom, yearTo,
                                { has_actions: 'true' })} />
            <Tile value={data.capa.completed} label="Completed"
                  to={incidents(`food safety near misses with a corrective action completed in ${y}`, yearFrom, yearTo,
                                { actions_state: 'completed' })} />
            <Tile value={data.capa.outstanding} label="Outstanding"
                  to={incidents(`food safety near misses with a corrective action outstanding in ${y}`, yearFrom, yearTo,
                                { actions_state: 'outstanding' })} />
          </div>
        </Section>

        <Section title={`Food safety near misses by month — ${data.total}`}>
          <MonthColumns rows={data.by_month}
                        series={[{ key: 'count', label: 'Near misses', cls: 'bg-primary-500' }]}
                        linkFor={(_, i) => {
                          const [f, t] = monthRange(`${y}-${pad(i + 1)}`)
                          return incidents(`food safety near misses in ${MONTH_NAMES[i]} ${y}`, f, t)
                        }} />
        </Section>
      </div>
    </div>
  )
}
