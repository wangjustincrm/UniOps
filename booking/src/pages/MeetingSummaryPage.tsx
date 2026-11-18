import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronLeft, ChevronRight, CalendarDays } from 'lucide-react'
import { cn, displayTimeZone, formatDate, todayISO } from '@/lib/utils'
import { useDaySummary } from '@/services/api'
import type { DaySummaryRoom, DaySummaryBooking } from '@/lib/types'

import { Input, Button, Badge } from '@uniops/shell'
// ── Date helpers ──────────────────────────────────────────────────────────────

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00`)
  d.setDate(d.getDate() + n)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function formatWeekday(iso: string): string {
  const d = new Date(`${iso}T00:00:00`)
  return `${d.toLocaleDateString('en-CA', { weekday: 'long' })}, ${formatDate(iso)}`
}

// ── Time helpers ──────────────────────────────────────────────────────────────

/** Parse "HH:MM" -> total minutes since midnight. */
function hhmm2min(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

/**
 * Extract {hour, minute} in a given IANA timezone from a Date using
 * Intl.DateTimeFormat. This is the canonical way to project a UTC instant
 * into an arbitrary timezone without relying on the browser's own TZ setting.
 */
function _partsInZone(d: Date, tz: string): { h: number; m: number } {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    hour: 'numeric',
    minute: 'numeric',
    hour12: false,
  }).formatToParts(d)
  // hour12:false can produce "24" at midnight on some platforms — normalise to 0
  const h = Number(parts.find(p => p.type === 'hour')!.value) % 24
  const m = Number(parts.find(p => p.type === 'minute')!.value)
  return { h, m }
}

/** Convert a UTC ISO datetime string to HH:MM in the display timezone. */
function toDisplayHHMM(utcIso: string, tz: string): string {
  const d = new Date(utcIso)
  const { h, m } = _partsInZone(d, tz)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

/** Convert a UTC ISO datetime to minutes-since-midnight in the display timezone. */
function toDisplayMin(utcIso: string, tz: string): number {
  const d = new Date(utcIso)
  const { h, m } = _partsInZone(d, tz)
  return h * 60 + m
}

/** Current time as minutes-since-midnight in the display timezone. */
function nowDisplayMin(tz: string): number {
  const { h, m } = _partsInZone(new Date(), tz)
  return h * 60 + m
}

// ── Grid constants ─────────────────────────────────────────────────────────────

const GUTTER_W = 56     // px — left hour label column
const ROOM_MIN_W = 180  // px — minimum room column width
const ROW_H = 64        // px per hour

// ── Room header ───────────────────────────────────────────────────────────────

function RoomHeader({ room, onClick }: { room: DaySummaryRoom; onClick: () => void }) {
  const isMaint = room.status === 'maintenance'
  return (
    <div
      onClick={onClick}
      className={cn(
        'flex flex-col gap-0.5 px-3 py-2 border-b border-r border-line cursor-pointer select-none',
        'hover:bg-surface-subtle transition-colors',
        isMaint && 'bg-surface-sunken',
      )}
      title={isMaint ? `${room.name} — Maintenance` : room.name}
    >
      <div className="flex items-center gap-1.5 min-w-0">
        <span className={cn('text-sm font-semibold truncate', isMaint ? 'text-fg-muted' : 'text-fg')}>
          {room.name}
        </span>
        {isMaint && (
          <Badge variant="warning" size="sm" className="shrink-0">
            Maintenance
          </Badge>
        )}
      </div>
      <div className="flex items-center gap-2 text-2xs text-fg-muted">
        <span>{room.code}</span>
        <span>·</span>
        <span>{room.capacity} pax</span>
      </div>
    </div>
  )
}

// ── Booking block ─────────────────────────────────────────────────────────────

interface BookingBlockProps {
  booking: DaySummaryBooking
  openStartMin: number
  openSpanMin: number
  displayTz: string
}

function BookingBlock({ booking, openStartMin, openSpanMin, displayTz }: BookingBlockProps) {
  const startMin = toDisplayMin(booking.starts_at, displayTz)
  const endMin   = toDisplayMin(booking.ends_at, displayTz)

  // Clamp to the visible span
  const clampedStart = Math.max(startMin, openStartMin)
  const clampedEnd   = Math.min(endMin, openStartMin + openSpanMin)

  if (clampedEnd <= clampedStart) return null

  const topPct    = ((clampedStart - openStartMin) / openSpanMin) * 100
  const heightPct = ((clampedEnd - clampedStart) / openSpanMin) * 100

  const startLabel = toDisplayHHMM(booking.starts_at, displayTz)
  const endLabel   = toDisplayHHMM(booking.ends_at, displayTz)
  const tooltip    = `${booking.title}\n${startLabel}–${endLabel}\n${booking.organizer_name}`

  return (
    <div
      title={tooltip}
      style={{ top: `${topPct}%`, height: `${heightPct}%` }}
      className={cn(
        'absolute inset-x-1 rounded-sm border-l-[3px] border-primary-700 bg-primary-700/10 px-1.5 py-0.5',
        'overflow-hidden select-none',
      )}
    >
      <p className="truncate text-2xs font-semibold text-primary-700 leading-tight">{booking.title}</p>
      <p className="truncate text-2xs text-primary-700/70 leading-tight">{startLabel}–{endLabel}</p>
      <p className="truncate text-2xs text-primary-700/60 leading-tight">{booking.organizer_name}</p>
    </div>
  )
}

// ── Current time indicator ────────────────────────────────────────────────────

interface CurrentTimeLineProps {
  openStartMin: number
  openSpanMin: number
  displayTz: string
}

function CurrentTimeLine({ openStartMin, openSpanMin, displayTz }: CurrentTimeLineProps) {
  const [currentMin, setCurrentMin] = useState(() => nowDisplayMin(displayTz))

  useEffect(() => {
    const id = setInterval(() => setCurrentMin(nowDisplayMin(displayTz)), 60_000)
    return () => clearInterval(id)
  }, [displayTz])

  if (currentMin < openStartMin || currentMin > openStartMin + openSpanMin) return null

  const topPct = ((currentMin - openStartMin) / openSpanMin) * 100

  return (
    <div
      className="absolute left-0 right-0 z-20 flex items-center pointer-events-none"
      style={{ top: `${topPct}%` }}
    >
      <div className="h-2 w-2 rounded-full bg-danger-solid -translate-x-1 shrink-0" />
      <div className="flex-1 border-t-2 border-danger-solid" />
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function MeetingSummaryPage() {
  const [selectedDate, setSelectedDate] = useState(todayISO)
  const navigate = useNavigate()
  const isToday = selectedDate === todayISO()

  const { data, isLoading, isError } = useDaySummary(selectedDate)

  // The plant's time zone (Company Settings) — all times follow it. The
  // backend's own `data.timezone` is no longer used for display.
  const displayTz = displayTimeZone()

  // Computed from API response (or defaults while loading)
  const openStartMin = data ? hhmm2min(data.open_start) : hhmm2min('08:00')
  const openEndMin   = data ? hhmm2min(data.open_end)   : hhmm2min('20:00')
  const openSpanMin  = openEndMin - openStartMin

  // Hour ticks for the gutter
  const startHour = Math.floor(openStartMin / 60)
  const endHour   = Math.ceil(openEndMin / 60)
  const hours: number[] = []
  for (let h = startHour; h <= endHour; h++) hours.push(h)

  // Bookings indexed by room_id (useMemo — rebuilt only when data changes)
  const bookingsByRoom = useMemo((): Map<string, DaySummaryBooking[]> => {
    const map = new Map<string, DaySummaryBooking[]>()
    if (!data) return map
    for (const b of data.bookings) {
      const list = map.get(b.room_id) ?? []
      list.push(b)
      map.set(b.room_id, list)
    }
    return map
  }, [data])

  // Count of bookings that match a visible room (for empty-state check)
  const visibleBookingCount = data
    ? data.bookings.filter(b => data.rooms.some(r => r.id === b.room_id)).length
    : 0

  const gridH = (openSpanMin / 60) * ROW_H

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* ── Header bar ─────────────────────────────────────────────────────── */}
      <div className="no-print flex shrink-0 items-center gap-3 border-b border-line bg-surface px-4 py-3">
        <CalendarDays className="h-5 w-5 text-primary-700" />
        <h1 className="text-base font-semibold text-fg">Meeting Summary</h1>

        <div className="mx-2 h-5 w-px bg-surface-strong" />

        {/* Today button */}
        <Button
          onClick={() => setSelectedDate(todayISO())}
          variant={isToday ? 'primary' : 'secondary'} size="xs" aria-pressed={isToday}
        >
          Today
        </Button>

        {/* Arrows */}
        <Button variant="subtle" size="icon-xs"
          onClick={() => setSelectedDate(d => addDays(d, -1))}
         
          aria-label="Previous day"
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <Button variant="subtle" size="icon-xs"
          onClick={() => setSelectedDate(d => addDays(d, 1))}
         
          aria-label="Next day"
        >
          <ChevronRight className="h-4 w-4" />
        </Button>

        {/* Date input */}
        <Input inputSize="sm"
          type="date"
          value={selectedDate}
          onChange={e => e.target.value && setSelectedDate(e.target.value)}
         
        />

        {/* Weekday label */}
        <span className="text-sm text-fg-muted">{formatWeekday(selectedDate)}</span>
      </div>

      {/* ── Loading / error states ─────────────────────────────────────────── */}
      {isLoading && (
        <div className="flex flex-1 items-center justify-center text-sm text-fg-muted">
          Loading schedule…
        </div>
      )}
      {isError && (
        <div className="flex flex-1 items-center justify-center text-sm text-danger-fg">
          Failed to load schedule. Please try again.
        </div>
      )}

      {/* ── No rooms ──────────────────────────────────────────────────────── */}
      {data && data.rooms.length === 0 && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-sm text-fg-muted">
          <CalendarDays className="h-8 w-8 opacity-30" />
          <p>No meeting rooms found.</p>
          <p className="text-xs">Ask an admin to configure rooms in Rooms Admin.</p>
        </div>
      )}

      {/* ── Day grid ──────────────────────────────────────────────────────── */}
      {data && data.rooms.length > 0 && (
        <div className="relative flex-1 overflow-auto">
          {/* Sticky room header row */}
          <div
            className="sticky top-0 z-10 flex bg-surface"
            style={{ paddingLeft: GUTTER_W }}
          >
            {data.rooms.map(room => (
              <div
                key={room.id}
                style={{ minWidth: ROOM_MIN_W, width: ROOM_MIN_W }}
                className="shrink-0"
              >
                <RoomHeader room={room} onClick={() => navigate(`/rooms/${room.id}`)} />
              </div>
            ))}
          </div>

          {/* Scroll body */}
          <div className="flex" style={{ minWidth: GUTTER_W + data.rooms.length * ROOM_MIN_W }}>
            {/* Time gutter */}
            <div
              className="sticky left-0 z-10 shrink-0 bg-surface border-r border-line relative"
              style={{ width: GUTTER_W, height: gridH }}
            >
              {hours.map(h => {
                const topPct = ((h * 60 - openStartMin) / openSpanMin) * 100
                if (topPct < 0 || topPct > 100) return null
                return (
                  <div
                    key={h}
                    className="absolute right-2 -translate-y-2 text-2xs text-fg-muted select-none"
                    style={{ top: `${topPct}%` }}
                  >
                    {String(h).padStart(2, '0')}:00
                  </div>
                )
              })}
            </div>

            {/* Room columns */}
            <div className="relative flex flex-1" style={{ height: gridH }}>
              {/* Hour grid lines (shared background) */}
              {hours.map(h => {
                const topPct = ((h * 60 - openStartMin) / openSpanMin) * 100
                if (topPct < 0 || topPct > 100) return null
                return (
                  <div
                    key={h}
                    className="absolute left-0 right-0 border-t border-line"
                    style={{ top: `${topPct}%` }}
                  />
                )
              })}
              {/* 30-min half-hour lines */}
              {hours.map(h => {
                const topPct = ((h * 60 + 30 - openStartMin) / openSpanMin) * 100
                if (topPct <= 0 || topPct >= 100) return null
                return (
                  <div
                    key={`${h}-30`}
                    className="absolute left-0 right-0 border-t border-line-subtle"
                    style={{ top: `${topPct}%` }}
                  />
                )
              })}

              {/* Current time indicator across all room columns */}
              {isToday && (
                <CurrentTimeLine openStartMin={openStartMin} openSpanMin={openSpanMin} displayTz={displayTz} />
              )}

              {/* Per-room column */}
              {data.rooms.map((room, idx) => {
                const roomBookings = bookingsByRoom.get(room.id) ?? []
                const isMaint = room.status === 'maintenance'
                return (
                  <div
                    key={room.id}
                    className={cn(
                      'relative shrink-0 border-r border-line',
                      isMaint && 'bg-surface-subtle/60',
                    )}
                    style={{ width: ROOM_MIN_W, height: '100%' }}
                  >
                    {/* No-meetings overlay (only on first column when all visible rooms empty) */}
                    {roomBookings.length === 0 && !isMaint && visibleBookingCount === 0 && idx === 0 && (
                      <div className="absolute inset-0 flex items-center justify-center">
                        <span className="text-2xs text-fg-faint select-none">No meetings scheduled</span>
                      </div>
                    )}
                    {roomBookings.map(b => (
                      <BookingBlock
                        key={b.id}
                        booking={b}
                        openStartMin={openStartMin}
                        openSpanMin={openSpanMin}
                        displayTz={displayTz}
                      />
                    ))}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
