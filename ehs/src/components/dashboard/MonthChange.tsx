/**
 * ★ UAT 14: "add a small number in the top right corner counting the number …
 * this month and an arrow pointing up if is an increase, or down if it is a
 * decrease compared to the previous month. Make the arrow pointing up dark
 * green and the arrow pointing down dark red."
 *
 * The arrow is the direction only — it says nothing about good or bad, and is
 * coloured as HSE asked. The words say the same for a screen reader and on
 * hover, so nothing depends on colour.
 */
import { ArrowDown, ArrowUp, Minus } from 'lucide-react'
import TabLink from '@/components/TabLink'
import { MONTH_NAMES } from './charts'

function monthName(ym: string): string {
  const [y, m] = ym.split('-').map(Number)
  return `${MONTH_NAMES[m - 1]} ${y}`
}

export function MonthChange({ now, before, month, previous, percent }: {
  now: number; before: number
  /** "2026-11" */
  month: string; previous: string
  /** Shown beside the arrow when given (the Food Safety dashboard). */
  percent?: number | null
}) {
  const up = now > before
  const down = now < before
  const words = `${now} in ${monthName(month)}, ${
    up ? 'up' : down ? 'down' : 'the same'} from ${before} in ${monthName(previous)}`
  return (
    <span className="inline-flex items-center gap-0.5 text-xs font-semibold tabular-nums text-neutral-700"
          title={words}>
      <span className="sr-only">{words}</span>
      <span aria-hidden>{now}</span>
      {up && <ArrowUp className="h-3.5 w-3.5 text-success-800" strokeWidth={3} aria-hidden />}
      {down && <ArrowDown className="h-3.5 w-3.5 text-danger-800" strokeWidth={3} aria-hidden />}
      {!up && !down && <Minus className="h-3.5 w-3.5 text-neutral-400" aria-hidden />}
      {percent !== undefined && percent !== null && (
        <span aria-hidden className={up ? 'text-success-800' : down ? 'text-danger-800' : 'text-neutral-500'}>
          {percent > 0 ? '+' : ''}{percent}%
        </span>
      )}
    </span>
  )
}

/** ★ UAT 14: "beside the 'HSE Dashboard' at the top of the page, create a
 *  similar title of 'Food Safety'. Clicking on Food safety would bring up a
 *  food safety dashboard." The one shown is the page's heading; the other is
 *  a link to it. */
export function DashboardTitles({ current }: { current: 'hse' | 'food' }) {
  const items = [
    { key: 'hse', label: 'HSE dashboard', to: '/dashboard' },
    { key: 'food', label: 'Food Safety', to: '/dashboard/food-safety' },
  ] as const
  return (
    <div className="flex flex-wrap items-baseline gap-x-3">
      {items.map((it, i) => (
        <span key={it.key} className="flex items-baseline gap-3">
          {i > 0 && <span aria-hidden className="text-lg text-neutral-300">|</span>}
          {it.key === current
            ? <h1 className="text-lg font-bold tracking-tight text-neutral-900">{it.label}</h1>
            : <TabLink to={it.to}
                       className="text-lg font-bold tracking-tight text-neutral-400 hover:text-primary-700">
                {it.label}
              </TabLink>}
        </span>
      ))}
    </div>
  )
}
