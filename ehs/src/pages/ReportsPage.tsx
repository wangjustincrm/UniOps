/**
 * The reporting tab.
 *
 * One builder over every record type rather than a page per report, which is
 * what HSE asked for. Pick what to count, how to group it, over which period —
 * the engine answers the rest, and the entity list tells this screen which
 * groupings are even possible, so nobody can assemble a question that errors.
 *
 * Bars rather than a chart library: the answer to "which area has the most
 * incidents" is a ranked list, and a ranked list drawn as bars needs no
 * dependency, renders on a phone, and prints.
 *
 * ★ Round 5 feedback (1 Oct): "add additional functionality, searching by type
 * of incident, toolbox talks etc." So a bar is a way in, not just a number:
 * opening one narrows the report to it and lists the records behind it, and
 * the narrowing stacks — first aid, then by area, then that area's records.
 */
import TabLink from '@/components/TabLink'
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, TrendingDown, TrendingUp, X } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate, formatDateTime } from '@/lib/formatDate'
import {
  LoadFailedNote, PageHeader, SectionTitle,
} from '@/components/ui'

interface Entity { key: string; label: string; dimensions: string[]; listable: boolean }
interface Dimension { key: string; label: string }
interface RecordRow { id: string; ref: string | null; title: string | null; at: string | null; path: string | null }
interface Records { total: number; rows: RecordRow[] }
/** One narrowing: a dimension, the value picked, and how that value reads. */
interface Filter { dim: string; value: string | null; label: string }
interface GroupRow { value: string | null; label: string | null; count: number }
interface CompareRow {
  value: string | null; label: string | null
  period_a: number; period_b: number; change: number; change_percent: number | null
}

const inputClass =
  'min-h-[40px] rounded-lg border border-neutral-300 bg-white px-2.5 text-sm ' +
  'focus:border-primary-400 focus:outline-none'

function thisYear(): [string, string] {
  const y = new Date().getFullYear()
  return [`${y}-01-01`, `${y + 1}-01-01`]
}
function lastYear(): [string, string] {
  const y = new Date().getFullYear() - 1
  return [`${y}-01-01`, `${y + 1}-01-01`]
}

type Period = 'all' | 'this_year' | 'last_year' | 'custom'

function periodBounds(period: Period, from: string, to: string): [string, string] | null {
  if (period === 'this_year') return thisYear()
  if (period === 'last_year') return lastYear()
  if (period === 'custom' && (from || to)) {
    // The "to" a person types is the last day they want included; the engine's
    // upper bound is exclusive, so it is moved on a day. Done on the string's
    // own numbers — never through Date, which would land a day early west of
    // Greenwich.
    let end = ''
    if (to) {
      const [y, m, d] = to.split('-').map(Number)
      const next = new Date(Date.UTC(y, m - 1, d + 1))
      end = next.toISOString().slice(0, 10)
    }
    return [from, end]
  }
  return null
}

function query(params: Record<string, string>, filters: Filter[]): string {
  const q = new URLSearchParams(params)
  // Nothing after the colon is the "Not recorded" bar.
  for (const f of filters) q.append('filter', `${f.dim}:${f.value ?? ''}`)
  return q.toString()
}

export default function ReportsPage() {
  const [entity, setEntity] = useState('incident')
  const [dimension, setDimension] = useState('incident_type')
  const [compare, setCompare] = useState(false)
  const [filters, setFilters] = useState<Filter[]>([])
  const [period, setPeriod] = useState<Period>('all')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [showRecords, setShowRecords] = useState(false)

  const entities = useQuery({
    queryKey: ['report-entities'],
    queryFn: () => api.get<Entity[]>('/api/v1/reports/entities'),
  })
  // The names come from the engine, so a grouping added there is named here
  // without a second list to keep in step.
  const dimensions = useQuery({
    queryKey: ['report-dimensions'],
    queryFn: () => api.get<Dimension[]>('/api/v1/reports/dimensions'),
  })
  const dimLabel = (key: string | undefined) =>
    dimensions.data?.find((d) => d.key === key)?.label ?? key ?? ''

  const current = entities.data?.find((e) => e.key === entity)
  const narrowed = new Set(filters.map((f) => f.dim))
  // Switching entity can leave a dimension the new one does not have; fall back
  // rather than asking the server a question it will refuse.
  const dim = current?.dimensions.includes(dimension) ? dimension : current?.dimensions[0]

  const bounds = periodBounds(period, from, to)
  const dated: Record<string, string> = {}
  if (bounds?.[0]) dated.date_from = bounds[0]
  if (bounds?.[1]) dated.date_to = bounds[1]

  const [aFrom, aTo] = lastYear()
  const [bFrom, bTo] = thisYear()

  const group = useQuery({
    queryKey: ['report-group', entity, dim, filters, dated],
    queryFn: () => api.get<GroupRow[]>(
      `/api/v1/reports/group?${query({ entity, dimension: dim ?? '', ...dated }, filters)}`),
    enabled: Boolean(dim) && !compare,
  })

  const comparison = useQuery({
    queryKey: ['report-compare', entity, dim, filters],
    queryFn: () => api.get<CompareRow[]>(
      `/api/v1/reports/compare?${query({
        entity, dimension: dim ?? '', a_from: aFrom, a_to: aTo, b_from: bFrom, b_to: bTo,
      }, filters)}`),
    enabled: Boolean(dim) && compare,
  })

  // Opened by narrowing to something, or asked for outright.
  const listing = Boolean(current?.listable) && !compare && (filters.length > 0 || showRecords)
  const records = useQuery({
    queryKey: ['report-records', entity, filters, dated],
    queryFn: () => api.get<Records>(
      `/api/v1/reports/records?${query({ entity, ...dated }, filters)}`),
    enabled: listing,
  })

  const chooseEntity = (key: string) => {
    // A narrowing belongs to the record type it was made on.
    setEntity(key); setFilters([]); setShowRecords(false)
  }

  /** Open a bar: narrow to it, and regroup by something not yet narrowed. */
  const narrow = (r: GroupRow) => {
    if (!dim) return
    const next = [...filters.filter((f) => f.dim !== dim),
                  { dim, value: r.value, label: r.label ?? 'Not recorded' }]
    setFilters(next)
    const taken = new Set(next.map((f) => f.dim))
    const regroup = current?.dimensions.find((d) => !taken.has(d))
    if (regroup) setDimension(regroup)
  }

  const max = useMemo(
    () => Math.max(1, ...(group.data ?? []).map((r) => r.count)),
    [group.data])

  return (
    <div className="p-4">
      <PageHeader title="Reports" />

      {/* ── Injury rates ─────────────────────────────────────────────────
          Round 9: "move the injury rate graph to the dashboard near the top."
          Moved, not copied — one place for the figure, worked out by the same
          function either way. */}
      <p className="mb-4 text-xs text-neutral-500">
        The recordable, lost-time and DART rates are on the{' '}
        <TabLink to="/dashboard" className="font-medium text-primary-700 hover:underline">dashboard</TabLink>.
      </p>

      {/* ── Builder ──────────────────────────────────────────────────────── */}
      <section className="rounded-xl border border-neutral-200 bg-white p-4">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <select className={inputClass} value={entity} aria-label="What to count"
                  onChange={(e) => chooseEntity(e.target.value)}>
            {entities.data?.map((e) => (
              <option key={e.key} value={e.key}>{e.label}</option>
            ))}
          </select>
          <span className="text-sm text-neutral-500">by</span>
          <select className={inputClass} value={dim ?? ''} aria-label="Group by"
                  onChange={(e) => setDimension(e.target.value)}>
            {current?.dimensions.map((d) => (
              <option key={d} value={d} disabled={narrowed.has(d)}>{dimLabel(d)}</option>
            ))}
          </select>
          <label className="ml-auto flex items-center gap-1.5 text-xs text-neutral-700">
            <input type="checkbox" checked={compare}
                   onChange={(e) => setCompare(e.target.checked)} />
            This year vs last
          </label>
        </div>

        {!compare && (
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <select className={inputClass} value={period} aria-label="Period"
                    onChange={(e) => setPeriod(e.target.value as Period)}>
              <option value="all">All time</option>
              <option value="this_year">This year</option>
              <option value="last_year">Last year</option>
              <option value="custom">Between dates…</option>
            </select>
            {period === 'custom' && (
              <>
                <input type="date" className={inputClass} value={from} aria-label="From"
                       onChange={(e) => setFrom(e.target.value)} />
                <span className="text-sm text-neutral-500">to</span>
                <input type="date" className={inputClass} value={to} aria-label="To"
                       onChange={(e) => setTo(e.target.value)} />
              </>
            )}
          </div>
        )}

        {filters.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-neutral-500">Only</span>
            {filters.map((f) => (
              <span key={f.dim}
                    className="inline-flex items-center gap-1 rounded-full bg-primary-50 py-1 pl-2.5 pr-1 text-xs text-primary-800">
                <span className="text-primary-600">{dimLabel(f.dim)}:</span> {f.label}
                <button type="button" aria-label={`Stop narrowing by ${dimLabel(f.dim)}`}
                        onClick={() => setFilters(filters.filter((x) => x.dim !== f.dim))}
                        className="rounded-full p-0.5 hover:bg-primary-100">
                  <X className="h-3.5 w-3.5" aria-hidden />
                </button>
              </span>
            ))}
            <button type="button" onClick={() => setFilters([])}
                    className="text-xs text-neutral-500 underline hover:text-neutral-700">
              Clear
            </button>
          </div>
        )}

        {!compare && (
          <>
            {group.isError && <LoadFailedNote what="This report" />}
            <ul className="flex flex-col gap-1">
              {group.data?.length === 0 && (
                <li className="py-6 text-center text-sm text-neutral-500">Nothing to show.</li>
              )}
              {group.data?.map((r) => (
                <li key={String(r.value)}>
                  <button type="button" onClick={() => narrow(r)}
                          title={`Only ${r.label}`}
                          className="flex w-full items-center gap-3 rounded-lg px-1 py-1 text-left hover:bg-neutral-50">
                    <span className="w-10 shrink-0 text-right text-sm font-semibold tabular-nums text-neutral-900">
                      {r.count}
                    </span>
                    <span className="h-5 shrink-0 rounded bg-primary-500"
                          style={{ width: `${Math.max(4, (r.count / max) * 55)}%` }} />
                    <span className="min-w-0 flex-1 truncate text-sm text-neutral-700">
                      {r.label}
                    </span>
                    <ChevronRight className="h-4 w-4 shrink-0 text-neutral-300" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
            {(group.data?.length ?? 0) > 0 && (
              <p className="mt-2 text-xs text-neutral-500">
                Pick a bar to narrow to it and see the records behind it.
              </p>
            )}

            {current?.listable && filters.length === 0 && !showRecords && (
              <button type="button" onClick={() => setShowRecords(true)}
                      className="mt-3 text-sm font-medium text-primary-700 hover:underline">
                List the records
              </button>
            )}
            {listing && (
              <div className="mt-4 border-t border-neutral-200 pt-3">
                <SectionTitle className="mb-2">
                  Records{records.data ? ` (${records.data.total})` : ''}
                </SectionTitle>
                {records.isError && <LoadFailedNote what="The records" />}
                {records.data && records.data.total > records.data.rows.length && (
                  <p className="mb-2 text-xs text-warning-700">
                    The newest {records.data.rows.length} of {records.data.total}. Narrow further to see the rest.
                  </p>
                )}
                <ul className="flex flex-col divide-y divide-neutral-100">
                  {records.data?.rows.length === 0 && (
                    <li className="py-4 text-center text-sm text-neutral-500">None.</li>
                  )}
                  {records.data?.rows.map((row) => {
                    const when = row.at
                      ? (row.at.length === 10 ? formatDate(row.at) : formatDateTime(row.at))
                      : ''
                    const body = (
                      <>
                        <span className="w-28 shrink-0 font-mono text-xs text-neutral-500">{row.ref}</span>
                        <span className="min-w-0 flex-1 truncate text-sm text-neutral-800">{row.title}</span>
                        <span className="shrink-0 text-xs tabular-nums text-neutral-500">{when}</span>
                      </>
                    )
                    return (
                      <li key={row.id}>
                        {row.path ? (
                          <TabLink to={row.path}
                                   className="flex items-center gap-3 py-2 hover:bg-neutral-50">
                            {body}
                          </TabLink>
                        ) : (
                          <div className="flex items-center gap-3 py-2">{body}</div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}
          </>
        )}

        {compare && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 text-left text-xs text-neutral-500">
                <th className="pb-1.5 font-medium">{dimLabel(dim)}</th>
                <th className="pb-1.5 text-right font-medium">Last year</th>
                <th className="pb-1.5 text-right font-medium">This year</th>
                <th className="pb-1.5 text-right font-medium">Change</th>
              </tr>
            </thead>
            <tbody>
              {comparison.data?.map((r) => (
                <tr key={String(r.value)} className="border-b border-neutral-100">
                  <td className="py-1.5 pr-2 text-neutral-800">{r.label}</td>
                  <td className="py-1.5 text-right tabular-nums text-neutral-500">{r.period_a}</td>
                  <td className="py-1.5 text-right font-semibold tabular-nums text-neutral-900">
                    {r.period_b}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">
                    {r.change === 0 ? (
                      <span className="text-neutral-400">—</span>
                    ) : (
                      <span className={`inline-flex items-center gap-0.5 font-medium ${
                        r.change > 0 ? 'text-danger-700' : 'text-primary-700'}`}>
                        {r.change > 0
                          ? <TrendingUp className="h-3.5 w-3.5" aria-hidden />
                          : <TrendingDown className="h-3.5 w-3.5" aria-hidden />}
                        {r.change > 0 ? '+' : ''}{r.change}
                        {/* No percentage against a zero baseline — it is not one. */}
                        {r.change_percent !== null && ` (${r.change_percent > 0 ? '+' : ''}${r.change_percent}%)`}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
