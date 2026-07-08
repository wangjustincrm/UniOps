/**
 * WeekStrip — 7 small day columns with busy-density from week_bookings.
 * Clicking a column selects that day (lifted to parent).
 */
import type { BookingSlimOut } from '@/lib/types'
import { cn } from '@/lib/utils'

interface Props {
  weekBookings: BookingSlimOut[]
  selectedDay: string        // YYYY-MM-DD
  onSelectDay: (day: string) => void
  /** Start of week — defaults to current Monday */
  weekStart?: string         // YYYY-MM-DD
}

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

/** Get Monday of the ISO week containing `day`. */
function getMondayOf(day: string): Date {
  const d = new Date(`${day}T00:00:00`)
  const dow = d.getDay() // 0=Sun
  const diff = (dow === 0 ? -6 : 1 - dow) // shift to Monday
  d.setDate(d.getDate() + diff)
  return d
}

function addDays(base: Date, n: number): Date {
  const d = new Date(base)
  d.setDate(d.getDate() + n)
  return d
}

function toYMD(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/** Count bookings whose starts_at falls on the given YYYY-MM-DD. */
function countOnDay(bookings: BookingSlimOut[], day: string): number {
  return bookings.filter((b) => b.starts_at.slice(0, 10) === day).length
}

export function WeekStrip({ weekBookings, selectedDay, onSelectDay, weekStart }: Props) {
  const monday = weekStart ? new Date(`${weekStart}T00:00:00`) : getMondayOf(selectedDay)
  const today = new Date().toISOString().slice(0, 10)

  const days = Array.from({ length: 7 }, (_, i) => {
    const date   = addDays(monday, i)
    const ymd    = toYMD(date)
    const count  = countOnDay(weekBookings, ymd)
    const label  = DAY_LABELS[i]
    const dayNum = date.getDate()
    return { ymd, count, label, dayNum }
  })

  // Max count for density scaling
  const maxCount = Math.max(...days.map((d) => d.count), 1)

  return (
    <div className="flex gap-1">
      {days.map(({ ymd, count, label, dayNum }) => {
        const isSelected = ymd === selectedDay
        const isToday    = ymd === today
        const density    = count / maxCount // 0–1

        return (
          <button
            key={ymd}
            type="button"
            onClick={() => onSelectDay(ymd)}
            className={cn(
              'flex flex-1 flex-col items-center rounded-lg border px-1 py-1.5 text-center transition-colors',
              isSelected
                ? 'border-[#085E5E] bg-[#085E5E]/10 text-[#085E5E]'
                : 'border-neutral-200 bg-white text-neutral-600 hover:border-neutral-300 hover:bg-neutral-50',
            )}
          >
            <span className={cn('text-[10px] font-medium uppercase tracking-wide', isSelected ? 'text-[#085E5E]' : 'text-neutral-400')}>
              {label}
            </span>
            <span className={cn(
              'mt-0.5 text-sm font-bold leading-none',
              isToday && !isSelected && 'text-[#085E5E]',
            )}>
              {dayNum}
            </span>
            {/* Busy density bar */}
            <div className="mt-1.5 h-1 w-full rounded-full bg-neutral-100 overflow-hidden">
              {count > 0 && (
                <div
                  className={cn(
                    'h-full rounded-full transition-all',
                    isSelected ? 'bg-[#085E5E]' : 'bg-[#085E5E]/50',
                  )}
                  style={{ width: `${Math.max(density * 100, 20)}%` }}
                />
              )}
            </div>
            {count > 0 && (
              <span className={cn(
                'mt-0.5 text-[9px]',
                isSelected ? 'text-[#085E5E]/80' : 'text-neutral-400',
              )}>
                {count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
