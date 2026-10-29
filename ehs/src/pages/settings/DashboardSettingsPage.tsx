/**
 * The dashboard's colours and chart styles, set once for everybody.
 *
 * Round 9: "when the days since last medical incident is greater than 15
 * change the colour … to green … red when the count is less than 5, and
 * yellow in between … allow for changing those numbers in the future", and
 * "a way to change the graph styles … like changing where people are getting
 * hurt from a bar graph to a circle graph". Round 10: "HSE will set the
 * numbers once for everybody under Settings. Chart style … set by HSE in
 * settings."
 *
 * Each row says which way is better in words, because that is the server's
 * fixed rule for the figure and the two numbers have to agree with it.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { PageHeader, primaryButton } from '@/components/ui'

interface Settings {
  thresholds: Record<string, { good: number; bad: number }>
  chart_styles: Record<string, string>
  metrics: { key: string; label: string; higher_is_better: boolean }[]
  charts: { key: string; label: string; styles: string[] }[]
}

const STYLE_NAMES: Record<string, string> = { bar: 'Bars', circle: 'Circle', body: 'Body picture' }
type Pair = { good: string; bad: string }

function asNumber(v: string): number | string | null {
  if (v.trim() === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : v
}

export default function DashboardSettingsPage() {
  const queryClient = useQueryClient()
  const { data, isPending, isError } = useQuery({
    queryKey: ['dashboard', 'settings'],
    queryFn: () => api.get<Settings>('/api/v1/dashboard/settings'),
  })
  const [pairs, setPairs] = useState<Record<string, Pair>>({})
  const [styles, setStyles] = useState<Record<string, string>>({})
  useEffect(() => {
    if (!data) return
    setPairs(Object.fromEntries(data.metrics.map((m) => {
      const p = data.thresholds[m.key]
      return [m.key, { good: p ? String(p.good) : '', bad: p ? String(p.bad) : '' }]
    })))
    setStyles(data.chart_styles)
  }, [data])

  const save = useMutation({
    mutationFn: () => api.put('/api/v1/dashboard/settings', {
      // Something that is not a number goes as typed, so the server refuses it
      // by name rather than it turning into "cleared" on the way.
      thresholds: Object.fromEntries(Object.entries(pairs).map(([k, p]) => [k, {
        good: asNumber(p.good), bad: asNumber(p.bad),
      }])),
      chart_styles: styles,
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  })
  const problems = save.error instanceof ApiError
    ? ((save.error.body as { detail?: { problems?: string[] } })?.detail?.problems ?? null) : null

  if (isPending) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  if (isError || !data) return <p className="p-4 text-sm text-danger-700">Could not load the dashboard settings.</p>
  const num = 'min-h-[40px] w-20 rounded-lg border border-neutral-300 px-2.5 text-sm tabular-nums'

  return (
    <div className="mx-auto max-w-3xl p-4">
      <PageHeader title="Dashboard"
                  subtitle="Colours and chart styles, the same for everybody who opens the dashboard." />

      <section className="mb-4 rounded-xl border border-neutral-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-neutral-900">Colours</h2>
        <p className="mb-3 text-xs text-neutral-500">
          A figure turns green past the first number and red past the second, yellow between them.
          Leave both empty for no colour.
        </p>
        <ul className="flex flex-col divide-y divide-neutral-100">
          {data.metrics.map((m) => {
            const p = pairs[m.key] ?? { good: '', bad: '' }
            const set = (k: keyof Pair, v: string) =>
              setPairs({ ...pairs, [m.key]: { ...p, [k]: v.replace(/[^0-9.]/g, '') } })
            const [gWord, bWord] = m.higher_is_better ? ['above', 'below'] : ['below', 'above']
            return (
              <li key={m.key} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2.5">
                <div className="min-w-0 flex-1 basis-56">
                  <p className="text-sm font-medium text-neutral-900">{m.label}</p>
                  <p className="text-[11px] text-neutral-500">
                    {m.higher_is_better ? 'More is better' : 'Fewer is better'}
                  </p>
                </div>
                <label className="flex items-center gap-1.5 text-xs text-neutral-700">
                  <span className="h-2.5 w-2.5 rounded-full bg-success-500" aria-hidden />
                  Green {gWord}
                  <input className={num} inputMode="decimal" value={p.good}
                         aria-label={`${m.label}: green ${gWord}`}
                         onChange={(e) => set('good', e.target.value)} />
                </label>
                <label className="flex items-center gap-1.5 text-xs text-neutral-700">
                  <span className="h-2.5 w-2.5 rounded-full bg-danger-500" aria-hidden />
                  Red {bWord}
                  <input className={num} inputMode="decimal" value={p.bad}
                         aria-label={`${m.label}: red ${bWord}`}
                         onChange={(e) => set('bad', e.target.value)} />
                </label>
              </li>
            )
          })}
        </ul>
      </section>

      <section className="mb-4 rounded-xl border border-neutral-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold text-neutral-900">Chart styles</h2>
        <ul className="flex flex-col gap-3">
          {data.charts.map((c) => (
            <li key={c.key}>
              <fieldset className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <legend className="mb-1 w-full text-sm font-medium text-neutral-900">{c.label}</legend>
                {c.styles.map((s) => (
                  <label key={s} className="flex min-h-[32px] items-center gap-1.5 text-sm text-neutral-700">
                    <input type="radio" name={c.key} checked={styles[c.key] === s}
                           onChange={() => setStyles({ ...styles, [c.key]: s })} />
                    {STYLE_NAMES[s] ?? s}
                  </label>
                ))}
              </fieldset>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[11px] text-neutral-500">
          The body picture shades each part by its injuries, using the Body Part list. An entry is
          placed on the picture under Settings → Lists → Body Part.
        </p>
      </section>

      {problems && (
        <div role="alert" className="mb-3 rounded-lg border border-warning-300 bg-warning-50 px-3 py-2 text-xs text-warning-800">
          <p className="font-semibold">Nothing was saved:</p>
          <ul className="mt-1 list-disc pl-4">{problems.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      )}
      {save.isError && !problems && (
        <p className="mb-3 text-xs text-danger-700">{(save.error as Error).message}</p>
      )}
      <div className="flex items-center justify-end gap-3">
        {save.isSuccess && !save.isPending && <span className="text-xs text-success-700">Saved</span>}
        <button type="button" className={primaryButton} disabled={save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}>
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}
