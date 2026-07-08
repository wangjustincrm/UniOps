import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronLeft, ChevronRight, CalendarDays } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useDaySummary } from '@/services/api'
import type { DaySummaryRoom, DaySummaryBooking } from '@/lib/types'

// ── Date helpers ──────────────────────────────────────────────────────────────

function todayIso(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00`)
  d.setDate(d.getDate() + n)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function formatWeekday(iso: string): string {
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString('en-CA', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })
}

// ── Time helpers ──────────────────────────────────────────────────────────────

/** Parse "HH:MM" -> total minutes since midnight. */
function hhmm2min(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

/** Convert a UTC ISO datetime string to local HH:MM. */
function toLocalHHMM(utcIso: string): string {
  const d = new Date(utcIso)
  return d.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit', hour12: false })
}

/** Convert a UTC ISO datetime to local minutes-since-midnight. */
function toLocalMin(utcIso: string): number {
  const d = new Date(utcIso)
  return d.getHours() * 60 + d.getMinutes()
}

/** Current time as local minutes-since-midnight. */
function nowLocalMin(): number {
  const d = new Date()
  return d.getHours() * 60 + d.getMinutes()
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
        'flex flex-col gap-0.5 px-3 py-2 border-b border-r border-neutral-200 cursor-pointer select-none',
        'hover:bg-neutral-50 transition-colors',
        isMaint && 'bg-neutral-100',
      )}
      title={isMaint ? `${room.name} — Maintenance` : room.name}
    >
      <div className="flex items-center gap-1.5 min-w-0">
        <span className={cn('text-sm font-semibold truncate', isMaint ? 'text-neutral-400' : 'text-neutral-800')}>
          {room.name}
        </span>
        {isMaint && (
          <span className="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 ring-1 ring-inset ring-amber-200">
            Maintenance
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 text-[11px] text-neutral-400">
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
}

function BookingBlock({ booking, openStartMin, openSpanMin }: BookingBlockProps) {
  const startMin = toLocalMin(booking.starts_at)
  const endMin   = toLocalMin(booking.ends_at)

  // Clamp to the visible span
  const clampedStart = Math.max(startMin, openStartMin)
  const clampedEnd   = Math.min(endMin, openStartMin + openSpanMin)

  if (clampedEnd <= clampedStart) return null

  const topPct    = ((clampedStart - openStartMin) / openSpanMin) * 100
  const heightPct = ((clampedEnd - clampedStart) / openSpanMin) * 100

  const startLabel = toLocalHHMM(booking.starts_at)
  const endLabel   = toLocalHHMM(booking.ends_at)
  const tooltip    = `${booking.title}\n${startLabel}–${endLabel}\n${booking.organizer_name}`

  return (
    <div
      title={tooltip}
      style={{ top: `${topPct}%`, height: `${heightPct}%` }}
      className={cn(
        'absolute inset-x-1 rounded-sm border-l-[3px] border-[#085E5E] bg-[#085E5E]/10 px-1.5 py-0.5',
        'overflow-hidden select-none',
      )}
    >
      <p className="truncate text-[11px] font-semibold text-[#085E5E] leading-tight">{booking.title}</p>
      <p className="truncate text-[10px] text-[#085E5E]/70 leading-tight">{startLabel}–{endLabel}</p>
      <p className="truncate text-[10px] text-[#085E5E]/60 leading-tight">{booking.organizer_name}</p>
    </div>
  )
}

// ── Current time indicator ────────────────────────────────────────────────────

interface CurrentTimeLineProps {
  openStartMin: number
  openSpanMin: number
}

function CurrentTimeLine({ openStartMin, openSpanMin }: CurrentTimeLineProps) {
  const [currentMin, setCurrentMin] = useState(nowLocalMin)

  useEffect(() => {
    const id = setInterval(() => setCurrentMin(nowLocalMin()), 60_000)
    return () => clearInterval(id)
  }, [])

  if (currentMin < openStartMin || currentMin > openStartMin + openSpanMin) return null

  const topPct = ((currentMin - openStartMin) / openSpanMin) * 100

  return (
    <div
      className="absolute left-0 right-0 z-20 flex items-center pointer-events-none"
      style={{ top: `${topPct}%` }}
    >
      <div className="h-2 w-2 rounded-full bg-red-500 -translate-x-1 shrink-0" />
      <div className="flex-1 border-t-2 border-red-500" />
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function MeetingSummaryPage() {
  const [selectedDate, setSelectedDate] = useState(todayIso)
  const navigate = useNavigate()
  const isToday = selectedDate === todayIso()

  const { data, isLoading, isError } = useDaySummary(selectedDate)

  // Computed from API response (or defaults while loading)
  const openStartMin = data ? hhmm2min(data.open_start) : hhmm2min('08:00')
  const openEndMin   = data ? hhmm2min(data.open_end)   : hhmm2min('20:00')
  const openSpanMin  = openEndMin - openStartMin

  // Hour ticks for the gutter
  const startHour = Math.floor(openStartMin / 60)
  const endHour   = Math.ceil(openEndMin / 60)
  const hours: number[] = []
  for (let h = startHour; h <= endHour; h++) hours.push(h)

  // Bookings indexed by room_id
  const bookingsByRoom = useCallback((): Map<string, DaySummaryBooking[]> => {
    const map = new Map<string, DaySummaryBooking[]>()
    if (!data) return map
    for (const b of data.bookings) {
      const list = map.get(b.room_id) ?? []
      list.push(b)
      map.set(b.room_id, list)
    }
    return map
  }, [data])()

  const gridH = (openSpanMin / 60) * ROW_H

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* ── Header bar ─────────────────────────────────────────────────────── */}
      <div className="no-print flex shrink-0 items-center gap-3 border-b border-neutral-200 bg-white px-4 py-3">
        <CalendarDays className="h-5 w-5 text-[#085E5E]" />
        <h1 className="text-base font-semibold text-neutral-800">Meeting Summary</h1>

        <div className="mx-2 h-5 w-px bg-neutral-200" />

        {/* Today button */}
        <button
          onClick={() => setSelectedDate(todayIso())}
          className={cn(
            'rounded-md border px-3 py-1 text-sm font-medium transition-colors',
            isToday
              ? 'border-[#085E5E] bg-[#085E5E] text-white'
              : 'border-neutral-300 bg-white text-neutral-700 hover:bg-neutral-50',
          )}
        >
          Today
        </button>

        {/* Arrows */}
        <button
          onClick={() => setSelectedDate(d => addDays(d, -1))}
          className="rounded p-1 text-neutral-500 hover:bg-neutral-100 transition-colors"
          aria-label="Previous day"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <button
          onClick={() => setSelectedDate(d => addDays(d, 1))}
          className="rounded p-1 text-neutral-500 hover:bg-neutral-100 transition-colors"
          aria-label="Next day"
        >
          <ChevronRight className="h-4 w-4" />
        </button>

        {/* Date input */}
        <input
          type="date"
          value={selectedDate}
          onChange={e => e.target.value && setSelectedDate(e.target.value)}
          className="rounded-md border border-neutral-300 px-2 py-1 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
        />

        {/* Weekday label */}
        <span className="text-sm text-neutral-500">{formatWeekday(selectedDate)}</span>
      </div>

      {/* ── Loading / error states ─────────────────────────────────────────── */}
      {isLoading && (
        <div className="flex flex-1 items-center justify-center text-sm text-neutral-400">
          Loading schedule…
        </div>
      )}
      {isError && (
        <div className="flex flex-1 items-center justify-center text-sm text-red-500">
          Failed to load schedule. Please try again.
        </div>
      )}

      {/* ── No rooms ──────────────────────────────────────────────────────── */}
      {data && data.rooms.length === 0 && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-sm text-neutral-400">
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
            className="sticky top-0 z-10 flex bg-white"
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
              className="sticky left-0 z-10 shrink-0 bg-white border-r border-neutral-200 relative"
              style={{ width: GUTTER_W, height: gridH }}
            >
              {hours.map(h => {
                const topPct = ((h * 60 - openStartMin) / openSpanMin) * 100
                if (topPct < 0 || topPct > 100) return null
                return (
                  <div
                    key={h}
                    className="absolute right-2 -translate-y-2 text-[10px] text-neutral-400 select-none"
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
                    className="absolute left-0 right-0 border-t border-neutral-200"
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
                    className="absolute left-0 right-0 border-t border-neutral-100"
                    style={{ top: `${topPct}%` }}
                  />
                )
              })}

              {/* Current time indicator across all room columns */}
              {isToday && (
                <CurrentTimeLine openStartMin={openStartMin} openSpanMin={openSpanMin} />
              )}

              {/* Per-room column */}
              {data.rooms.map((room, idx) => {
                const roomBookings = bookingsByRoom.get(room.id) ?? []
                const isMaint = room.status === 'maintenance'
                return (
                  <div
                    key={room.id}
                    className={cn(
                      'relative shrink-0 border-r border-neutral-200',
                      isMaint && 'bg-neutral-50/60',
                    )}
                    style={{ width: ROOM_MIN_W, height: '100%' }}
                  >
                    {/* No-meetings overlay (only on first column when all rooms empty) */}
                    {roomBookings.length === 0 && !isMaint && data.bookings.length === 0 && idx === 0 && (
                      <div className="absolute inset-0 flex items-center justify-center">
                        <span className="text-[11px] text-neutral-300 select-none">No meetings scheduled</span>
                      </div>
                    )}
                    {roomBookings.map(b => (
                      <BookingBlock
                        key={b.id}
                        booking={b}
                        openStartMin={openStartMin}
                        openSpanMin={openSpanMin}
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
