/**
 * Total hours worked, one figure a month.
 *
 * This is the divisor for TRIR, LTIFR and DART — the numbers a safety
 * programme is actually judged on. UniOps holds no payroll data, so HSE enters
 * one figure a month here, and asked that it stay editable: "the numbers will
 * be editable in that is they can be changed after the fact in case there is a
 * correction that needs to be made."
 *
 * A missing month is shown as missing rather than as zero. The reason is worth
 * knowing: a short divisor makes an injury rate read *higher* than it really
 * is, and there is nothing in the resulting number to reveal that. So the page
 * counts what is entered, and the reports name the gaps.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import {
  Card, ErrorNote, PageHeader, inputClass, primaryButton,
} from '@/components/ui'

interface MonthRow {
  id: string; year: number; month: number; hours: string
  note: string | null; entered_by_name: string | null; entered_at: string | null
}
interface YearOut {
  year: number; months: MonthRow[]
  total: string; months_found: number; months_expected: number
  missing: string[]; note: string | null
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November', 'December']

export default function HoursWorkedPage() {
  const { can } = usePermissions()
  const mayWrite = can('ehs.settings.manage')
  const queryClient = useQueryClient()
  const thisYear = new Date().getFullYear()
  const [year, setYear] = useState(thisYear)
  const [draft, setDraft] = useState<Record<number, string>>({})
  const [saved, setSaved] = useState<number | null>(null)

  const data = useQuery({
    queryKey: ['hours-worked', year],
    queryFn: () => api.get<YearOut>(`/api/v1/hours-worked?year=${year}`),
  })

  useEffect(() => { setDraft({}); setSaved(null) }, [year])

  const save = useMutation({
    mutationFn: ({ month, hours }: { month: number; hours: string }) =>
      api.put('/api/v1/hours-worked', { year, month, hours: Number(hours) }),
    onSuccess: (_r, vars) => {
      setSaved(vars.month)
      setDraft((d) => { const next = { ...d }; delete next[vars.month]; return next })
      queryClient.invalidateQueries({ queryKey: ['hours-worked', year] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['report-rates'] })
      setTimeout(() => setSaved(null), 2000)
    },
  })

  const byMonth = new Map((data.data?.months ?? []).map((m) => [m.month, m]))
  const total = Number(data.data?.total ?? 0)

  return (
    <div className="p-4">
      <PageHeader
        title="Hours worked"
        subtitle="One figure a month, from payroll. Every injury rate is divided by this."
      >
        <select className={`${inputClass} w-28`} value={year}
                onChange={(e) => setYear(Number(e.target.value))}>
          {[thisYear + 1, thisYear, thisYear - 1, thisYear - 2].map((y) => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
      </PageHeader>

      <div className="mb-4 flex flex-wrap gap-2">
        <Stat label="Entered so far" value={total ? total.toLocaleString() : '—'} />
        <Stat label="Months on record"
              value={`${data.data?.months_found ?? 0} of ${data.data?.months_expected ?? 0}`}
              tone={(data.data?.missing.length ?? 0) > 0 ? 'warn' : 'ok'} />
      </div>

      {data.data?.note && (
        <p className="mb-4 rounded-lg bg-warning-50 px-3 py-2 text-sm text-warning-700">
          {data.data.note}
        </p>
      )}

      <ul className="flex flex-col gap-1.5">
        {MONTHS.map((name, index) => {
          const month = index + 1
          const row = byMonth.get(month)
          const value = draft[month] ?? (row ? String(Number(row.hours)) : '')
          const changed = draft[month] !== undefined && draft[month] !== ''
          return (
            <li key={month}>
              <Card className="!p-2.5">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <span className="w-24 shrink-0 text-sm font-medium text-neutral-800">
                    {name}
                  </span>
                  <input
                    className={`${inputClass} w-40`}
                    inputMode="decimal"
                    disabled={!mayWrite}
                    value={value}
                    placeholder="not entered"
                    onChange={(e) =>
                      setDraft((d) => ({ ...d, [month]: e.target.value.replace(/[^0-9.]/g, '') }))}
                  />
                  {mayWrite && changed && (
                    <button type="button" className={primaryButton}
                            disabled={save.isPending}
                            onClick={() => {
                              if (!save.isPending) save.mutate({ month, hours: draft[month] })
                            }}>
                      Save
                    </button>
                  )}
                  {saved === month && (
                    <span className="flex items-center gap-1 text-xs font-medium text-success-700">
                      <Check className="h-3.5 w-3.5" aria-hidden />
                      Saved
                    </span>
                  )}
                  {row?.entered_at && !changed && (
                    <span className="text-xs text-neutral-500">
                      {row.entered_by_name} · {formatDateTime(row.entered_at)}
                      {row.note && ` · ${row.note}`}
                    </span>
                  )}
                </div>
              </Card>
            </li>
          )
        })}
      </ul>

      <ErrorNote error={save.error} />

      <p className="mt-4 text-xs text-neutral-500">
        Correcting a month is expected — enter the new figure over the old one.
        The rates recalculate from the total.
      </p>
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'ok' | 'warn' }) {
  const colour = tone === 'warn' ? 'text-warning-700'
    : tone === 'ok' ? 'text-success-700' : 'text-neutral-900'
  return (
    <div className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2.5">
      <p className="text-xs text-neutral-500">{label}</p>
      <p className={`font-mono text-lg font-bold tabular-nums ${colour}`}>{value}</p>
    </div>
  )
}
