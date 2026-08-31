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
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { TrendingDown, TrendingUp } from 'lucide-react'
import { api } from '@/lib/api'

interface Entity { key: string; label: string; dimensions: string[] }
interface GroupRow { value: string | null; label: string | null; count: number }
interface CompareRow {
  value: string | null; label: string | null
  period_a: number; period_b: number; change: number; change_percent: number | null
}
interface Rates {
  recordable: number; lost_time: number; dart_cases: number
  hours_worked: number | null; trir: number | null
  lost_time_rate: number | null; dart_rate: number | null; note: string | null
}

const DIMENSION_LABELS: Record<string, string> = {
  location: 'Area', department: 'Department', shift: 'Shift', category: 'Category',
  injury_class: 'Injury classification', status: 'Status', priority: 'Priority',
  rating: 'Risk rating', owner: 'Owner', course: 'Course', delivery: 'Delivery',
  month: 'Month',
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

export default function ReportsPage() {
  const [entity, setEntity] = useState('incident')
  const [dimension, setDimension] = useState('location')
  const [compare, setCompare] = useState(false)
  const [hours, setHours] = useState('')

  const entities = useQuery({
    queryKey: ['report-entities'],
    queryFn: () => api.get<Entity[]>('/api/v1/reports/entities'),
  })

  const current = entities.data?.find((e) => e.key === entity)
  // Switching entity can leave a dimension the new one does not have; fall back
  // rather than asking the server a question it will refuse.
  const dim = current?.dimensions.includes(dimension) ? dimension : current?.dimensions[0]

  const [aFrom, aTo] = lastYear()
  const [bFrom, bTo] = thisYear()

  const group = useQuery({
    queryKey: ['report-group', entity, dim],
    queryFn: () => api.get<GroupRow[]>(
      `/api/v1/reports/group?entity=${entity}&dimension=${dim}`),
    enabled: Boolean(dim) && !compare,
  })

  const comparison = useQuery({
    queryKey: ['report-compare', entity, dim],
    queryFn: () => api.get<CompareRow[]>(
      `/api/v1/reports/compare?entity=${entity}&dimension=${dim}` +
      `&a_from=${aFrom}&a_to=${aTo}&b_from=${bFrom}&b_to=${bTo}`),
    enabled: Boolean(dim) && compare,
  })

  const rates = useQuery({
    queryKey: ['report-rates', hours],
    queryFn: () => api.get<Rates>(
      `/api/v1/reports/injury-rates${hours ? `?hours_worked=${hours}` : ''}`),
  })

  const max = useMemo(
    () => Math.max(1, ...(group.data ?? []).map((r) => r.count)),
    [group.data])

  return (
    <div className="p-4">
      <h1 className="mb-4 text-lg font-bold tracking-tight text-neutral-900">Reports</h1>

      {/* ── Injury rates ─────────────────────────────────────────────────── */}
      <section className="mb-6 rounded-xl border border-neutral-200 bg-white p-4">
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wider text-neutral-400">
          Injury rates
        </h2>
        <div className="grid grid-cols-3 gap-3">
          {([['TRIR', rates.data?.trir], ['Lost time', rates.data?.lost_time_rate],
             ['DART', rates.data?.dart_rate]] as const).map(([label, value]) => (
            <div key={label} className="rounded-lg bg-neutral-50 p-3">
              <p className="text-xs text-neutral-500">{label}</p>
              <p className="mt-0.5 text-xl font-bold tabular-nums text-neutral-900">
                {value ?? '—'}
              </p>
            </div>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="text-xs text-neutral-600">Hours worked this year</label>
          <input className={`${inputClass} w-32`} inputMode="numeric" value={hours}
                 onChange={(e) => setHours(e.target.value.replace(/[^0-9]/g, ''))}
                 placeholder="from payroll" />
          <span className="text-xs text-neutral-500">
            {rates.data?.recordable ?? 0} recordable · {rates.data?.lost_time ?? 0} lost time
          </span>
        </div>
        {rates.data?.note && (
          <p className="mt-2 rounded-lg bg-warning-50 px-3 py-2 text-xs text-warning-700">
            {rates.data.note}
          </p>
        )}
      </section>

      {/* ── Builder ──────────────────────────────────────────────────────── */}
      <section className="rounded-xl border border-neutral-200 bg-white p-4">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <select className={inputClass} value={entity}
                  onChange={(e) => setEntity(e.target.value)}>
            {entities.data?.map((e) => (
              <option key={e.key} value={e.key}>{e.label}</option>
            ))}
          </select>
          <span className="text-sm text-neutral-500">by</span>
          <select className={inputClass} value={dim ?? ''}
                  onChange={(e) => setDimension(e.target.value)}>
            {current?.dimensions.map((d) => (
              <option key={d} value={d}>{DIMENSION_LABELS[d] ?? d}</option>
            ))}
          </select>
          <label className="ml-auto flex items-center gap-1.5 text-xs text-neutral-700">
            <input type="checkbox" checked={compare}
                   onChange={(e) => setCompare(e.target.checked)} />
            This year vs last
          </label>
        </div>

        {!compare && (
          <ul className="flex flex-col gap-1.5">
            {group.data?.length === 0 && (
              <li className="py-6 text-center text-sm text-neutral-500">Nothing to show.</li>
            )}
            {group.data?.map((r) => (
              <li key={String(r.value)} className="flex items-center gap-3">
                <span className="w-10 shrink-0 text-right text-sm font-semibold tabular-nums text-neutral-900">
                  {r.count}
                </span>
                <span className="h-5 shrink-0 rounded bg-primary-500"
                      style={{ width: `${Math.max(4, (r.count / max) * 55)}%` }} />
                <span className="min-w-0 flex-1 truncate text-sm text-neutral-700">
                  {r.label}
                </span>
              </li>
            ))}
          </ul>
        )}

        {compare && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 text-left text-xs text-neutral-500">
                <th className="pb-1.5 font-medium">{DIMENSION_LABELS[dim ?? ''] ?? dim}</th>
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
