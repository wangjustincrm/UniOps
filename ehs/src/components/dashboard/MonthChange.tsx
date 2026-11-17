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

/**
 * ★ UAT 15, question I: "a increasing number which is bad … should have an up
 * arrow in red. a decreasing number is good and should be green. Make the
 * number and the arrows bigger, 1/2 the size of the overall number." So the
 * colour says better or worse: red when it got worse, green when better.
 * Most figures here count harm, where more is worse; a few count reporting
 * (near misses, hazard assessments), where more is better — those pass
 * `upIsGood`. TC-13: the size is half the figure it sits beside.
 */
export function MonthChange({ now, before, month, previous, percent, upIsGood = false, size = 'box' }: {
  now: number; before: number
  /** "2026-11" */
  month: string; previous: string
  /** Shown beside the arrow when given (the Food Safety dashboard). */
  percent?: number | null
  upIsGood?: boolean
  /** Half of a box's 36px number, or of a panel's or tile's 30px. */
  size?: 'box' | 'tile'
}) {
  const up = now > before
  const down = now < before
  // ★ Review R16: this month is usually not over, and last month is whole —
  // said, so an arrow down on the 3rd is not read as an improvement.
  const today = new Date()
  const current = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}` === month
  const words = `${now} in ${monthName(month)}${current ? ' so far' : ''}, ${
    up ? 'up' : down ? 'down' : 'the same'} from ${before} in all of ${monthName(previous)}`
  const better = upIsGood ? up : down
  const worse = upIsGood ? down : up
  const tone = better ? 'text-success-700' : worse ? 'text-danger-700' : 'text-neutral-500'
  // UAT 16, O: "make the fonts 2 points larger."
  const text = size === 'box' ? 'text-xl' : 'text-[17px]'
  const icon = size === 'box' ? 'h-6 w-6' : 'h-5 w-5'
  return (
    <span className={`inline-flex items-center gap-0.5 font-bold leading-none tabular-nums text-neutral-800 ${text}`}
          title={`${words}${better ? ' — better' : worse ? ' — worse' : ''}`}>
      <span className="sr-only">{words}{better ? ', better' : worse ? ', worse' : ''}</span>
      <span aria-hidden>{now}</span>
      {up && <ArrowUp className={`${icon} ${tone}`} strokeWidth={3} aria-hidden />}
      {down && <ArrowDown className={`${icon} ${tone}`} strokeWidth={3} aria-hidden />}
      {!up && !down && <Minus className={`${icon} text-neutral-400`} aria-hidden />}
      {percent !== undefined && percent !== null && (
        <span aria-hidden className={`ml-0.5 ${tone}`}>
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
