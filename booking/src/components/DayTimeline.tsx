/**
 * DayTimeline — horizontal 15-min grid bar for a single day.
 *
 * Props:
 *   openStart  — "HH:MM" or "HH:MM:SS"; defaults to "08:00"
 *   openEnd    — "HH:MM" or "HH:MM:SS"; defaults to "20:00"
 *   bookings   — BookingSlimOut[] to render as filled blocks
 *   day        — "YYYY-MM-DD" string; used to detect today + filter bookings
 */
import type { BookingSlimOut } from '@/lib/types'
import { cn } from '@/lib/utils'

interface Props {
  openStart: string | null | undefined
  openEnd: string | null | undefined
  bookings: BookingSlimOut[]
  day: string // YYYY-MM-DD
}

/** Parse "HH:MM" or "HH:MM:SS" → minutes since midnight. */
function hmToMinutes(hm: string): number {
  const parts = hm.split(':')
  return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10)
}

/** Format ISO datetime to "HH:MM" in local time. */
function isoToHM(iso: string): string {
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export function DayTimeline({ openStart, openEnd, bookings, day }: Props) {
  const startMin = hmToMinutes(openStart?.slice(0, 5) ?? '08:00')
  const endMin   = hmToMinutes(openEnd?.slice(0, 5)   ?? '20:00')
  const spanMin  = endMin - startMin
  if (spanMin <= 0) return null

  // Filter bookings that overlap this day
  const dayStart = new Date(`${day}T00:00:00`)
  const dayEnd   = new Date(`${day}T23:59:59`)
  const dayBookings = bookings.filter((b) => {
    const s = new Date(b.starts_at)
    const e = new Date(b.ends_at)
    return s < dayEnd && e > dayStart
  })

  const today = new Date().toISOString().slice(0, 10)
  const isToday = day === today

  // Current time marker position (only for today)
  let nowPct: number | null = null
  if (isToday) {
    const now = new Date()
    const nowMin = now.getHours() * 60 + now.getMinutes()
    const pct = ((nowMin - startMin) / spanMin) * 100
    if (pct >= 0 && pct <= 100) nowPct = pct
  }

  // Hour tick marks
  const startHour = Math.ceil(startMin / 60)
  const endHour   = Math.floor(endMin / 60)
  const hourTicks: { hour: number; pct: number }[] = []
  for (let h = startHour; h <= endHour; h++) {
    const pct = ((h * 60 - startMin) / spanMin) * 100
    if (pct >= 0 && pct <= 100) hourTicks.push({ hour: h, pct })
  }

  return (
    <div className="select-none">
      {/* Hour labels row */}
      <div className="relative h-4 mb-0.5">
        {hourTicks.map(({ hour, pct }) => (
          <span
            key={hour}
            className="absolute -translate-x-1/2 text-[10px] text-neutral-400"
            style={{ left: `${pct}%` }}
          >
            {String(hour).padStart(2, '0')}:00
          </span>
        ))}
      </div>

      {/* Timeline bar */}
      <div className="relative h-8 rounded overflow-hidden bg-neutral-100 border border-neutral-200">
        {/* Hour grid lines */}
        {hourTicks.map(({ hour, pct }) => (
          <div
            key={hour}
            className="absolute top-0 bottom-0 w-px bg-neutral-200"
            style={{ left: `${pct}%` }}
          />
        ))}

        {/* Booking blocks */}
        {dayBookings.map((b) => {
          const bStart = new Date(b.starts_at)
          const bEnd   = new Date(b.ends_at)
          const bStartMin = bStart.getHours() * 60 + bStart.getMinutes()
          const bEndMin   = bEnd.getHours() * 60 + bEnd.getMinutes()

          // Clamp to open hours
          const clampedStart = Math.max(bStartMin, startMin)
          const clampedEnd   = Math.min(bEndMin, endMin)
          if (clampedEnd <= clampedStart) return null

          const left  = ((clampedStart - startMin) / spanMin) * 100
          const width = ((clampedEnd - clampedStart) / spanMin) * 100

          const tooltip = `${b.title}\n${isoToHM(b.starts_at)}–${isoToHM(b.ends_at)}\n${b.organizer_name}`

          return (
            <div
              key={b.id}
              title={tooltip}
              className={cn(
                'absolute top-0.5 bottom-0.5 rounded-sm',
                'bg-[#085E5E]/70 hover:bg-[#085E5E]/90 transition-colors',
              )}
              style={{ left: `${left}%`, width: `${Math.max(width, 0.5)}%` }}
            >
              {width > 8 && (
                <span className="absolute inset-0 flex items-center px-1 overflow-hidden">
                  <span className="text-[9px] text-white font-medium truncate leading-none">
                    {b.title}
                  </span>
                </span>
              )}
            </div>
          )
        })}

        {/* Current-time marker */}
        {nowPct !== null && (
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-red-500 z-10"
            style={{ left: `${nowPct}%` }}
          >
            <div className="absolute -top-0.5 -translate-x-1/2 h-1.5 w-1.5 rounded-full bg-red-500" />
          </div>
        )}
      </div>

      {/* Open hours label */}
      <div className="mt-1 flex justify-between text-[10px] text-neutral-400">
        <span>{openStart?.slice(0, 5) ?? '08:00'}</span>
        <span>{openEnd?.slice(0, 5) ?? '20:00'}</span>
      </div>
    </div>
  )
}
