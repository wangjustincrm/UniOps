import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Users, Clock, AlertCircle,
  Tv, Presentation, PenLine, Video, Phone,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useRoomList, useRoomAvailability, type RoomListFilters } from '@/services/api'
import type { RoomWithStatusOut, RoomStatusNow, EquipmentOption } from '@/lib/types'
import { EQUIPMENT_OPTIONS, EQUIPMENT_LABELS, ROOM_TYPES, ROOM_TYPE_LABELS } from '@/lib/types'
import { ROOM_STATUS_STYLE, ROOM_STATUS_LABEL } from '@/lib/roomStatus'

// ── Equipment icon map ────────────────────────────────────────────────────────

const EQUIPMENT_ICONS: Record<EquipmentOption, LucideIcon> = {
  tv:         Tv,
  projector:  Presentation,
  whiteboard: PenLine,
  video_conf: Video,
  phone_conf: Phone,
}

// ── Status badge ──────────────────────────────────────────────────────────────

function RoomStatusBadge({ status }: { status: RoomStatusNow }) {
  return (
    <span className={cn(
      'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
      ROOM_STATUS_STYLE[status],
    )}>
      {ROOM_STATUS_LABEL[status]}
    </span>
  )
}

// ── Time slot helpers ─────────────────────────────────────────────────────────

/** Build list of HH:MM time strings on 15-minute steps for the given range. */
function buildTimeOptions(startHour: number, endHour: number): string[] {
  const opts: string[] = []
  for (let h = startHour; h <= endHour; h++) {
    for (const m of [0, 15, 30, 45]) {
      if (h === endHour && m > 0) break
      opts.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`)
    }
  }
  return opts
}

const TIME_OPTIONS = buildTimeOptions(6, 23)

/** Combine a YYYY-MM-DD date string and HH:MM time string into an ISO datetime with UTC Z suffix. */
function toISO(date: string, time: string): string {
  return new Date(`${date}T${time}:00`).toISOString()
}

// ── Skeleton loader ───────────────────────────────────────────────────────────

function RoomCardSkeleton() {
  return (
    <div className="animate-pulse rounded-xl border border-neutral-200 bg-white p-4 flex flex-col gap-3">
      <div className="h-4 w-3/4 rounded bg-neutral-200" />
      <div className="h-3 w-1/2 rounded bg-neutral-100" />
      <div className="h-3 w-1/3 rounded bg-neutral-100" />
      <div className="flex gap-2 mt-auto">
        <div className="h-8 w-16 rounded bg-neutral-100" />
        <div className="ml-auto h-8 w-20 rounded bg-neutral-200" />
      </div>
    </div>
  )
}

// ── Room card ─────────────────────────────────────────────────────────────────

function RoomCard({
  room,
  onBook,
}: {
  room: RoomWithStatusOut
  onBook: (room: RoomWithStatusOut) => void
}) {
  const locationParts = [room.building, room.floor, room.area].filter(Boolean)
  const locationLine = locationParts.length > 0 ? locationParts.join(' · ') : null

  const nextTime = room.next_meeting_at
    ? new Date(room.next_meeting_at).toLocaleTimeString('en-CA', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    : null

  const isBookable = room.status === 'available'

  return (
    <div className="flex flex-col rounded-xl border border-neutral-200 bg-white p-4 gap-3 shadow-sm hover:shadow-md transition-shadow">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-neutral-900 truncate">{room.name}</p>
          <p className="text-xs text-neutral-400 font-mono mt-0.5">{room.code}</p>
        </div>
        <RoomStatusBadge status={room.status_now} />
      </div>

      {/* Location */}
      {locationLine && (
        <p className="text-sm text-neutral-500 truncate">{locationLine}</p>
      )}

      {/* Capacity */}
      <div className="flex items-center gap-1.5 text-sm text-neutral-600">
        <Users className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
        <span>Capacity: {room.capacity}</span>
      </div>

      {/* Equipment icons */}
      {room.equipment.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {room.equipment.map((eq) => {
            const Icon = EQUIPMENT_ICONS[eq as EquipmentOption]
            const label = EQUIPMENT_LABELS[eq as EquipmentOption] ?? eq
            if (!Icon) return null
            return (
              <span
                key={eq}
                title={label}
                className="inline-flex items-center gap-1 rounded-md bg-neutral-50 px-1.5 py-0.5 text-xs text-neutral-500 ring-1 ring-neutral-200"
              >
                <Icon className="h-3 w-3" />
                {label}
              </span>
            )
          })}
        </div>
      )}

      {/* Next meeting */}
      {nextTime && (
        <div className="flex items-center gap-1.5 text-xs text-neutral-400">
          <Clock className="h-3 w-3" />
          Next meeting {nextTime}
        </div>
      )}

      {/* Actions */}
      <div className="mt-auto pt-2">
        <button
          onClick={() => onBook(room)}
          disabled={!isBookable}
          className={cn(
            'w-full rounded-lg px-3 py-2 text-sm font-medium transition-colors',
            isBookable
              ? 'bg-[#085E5E] text-white hover:bg-[#074f4f]'
              : 'bg-neutral-100 text-neutral-400 cursor-not-allowed',
          )}
        >
          {isBookable ? 'Book' : 'Unavailable'}
        </button>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RoomsPage() {
  const navigate = useNavigate()
  const today = new Date().toISOString().slice(0, 10)

  // Filter state
  const [date, setDate] = useState<string>(today)
  const [startTime, setStartTime] = useState<string>('')
  const [endTime, setEndTime] = useState<string>('')
  const [attendees, setAttendees] = useState<string>('')
  const [campus, setCampus] = useState<string>('')
  const [building, setBuilding] = useState<string>('')
  const [floor, setFloor] = useState<string>('')
  const [area, setArea] = useState<string>('')
  const [selectedEquipment, setSelectedEquipment] = useState<string[]>([])
  const [roomType, setRoomType] = useState<string>('')

  // Build filter object for the service calls
  const filters: RoomListFilters = useMemo(() => {
    const f: RoomListFilters = {}
    if (campus.trim())    f.campus   = campus.trim()
    if (building.trim())  f.building = building.trim()
    if (floor.trim())     f.floor    = floor.trim()
    if (area.trim())      f.area     = area.trim()
    if (attendees && Number(attendees) > 0) f.min_capacity = Number(attendees)
    if (selectedEquipment.length > 0) f.equipment = selectedEquipment
    if (roomType) f.room_type = roomType
    return f
  }, [campus, building, floor, area, attendees, selectedEquipment, roomType])

  // Whether we have a full time window
  const hasWindow = !!(date && startTime && endTime && startTime < endTime)
  const window = hasWindow
    ? { starts_at: toISO(date, startTime), ends_at: toISO(date, endTime) }
    : null

  // Availability query (only when window is set)
  const availQuery = useRoomAvailability(window, filters)

  // List query (only when no availability window is set)
  const listQuery = useRoomList(filters, !hasWindow)

  const { data, isLoading, error } = hasWindow ? availQuery : listQuery

  const rooms: RoomWithStatusOut[] = data ?? []

  function handleBook(room: RoomWithStatusOut) {
    const params = new URLSearchParams()
    if (window) {
      params.set('start', window.starts_at)
      params.set('end', window.ends_at)
    }
    const qs = params.toString()
    navigate(`/rooms/${room.id}${qs ? '?' + qs : ''}`)
  }

  function toggleEquipment(eq: string) {
    setSelectedEquipment(prev =>
      prev.includes(eq) ? prev.filter(e => e !== eq) : [...prev, eq],
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        {/* Page header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-neutral-900">Meeting Rooms</h1>
          <p className="mt-1 text-sm text-neutral-500">
            {hasWindow
              ? 'Showing available rooms for the selected time window.'
              : 'Showing all rooms with live status. Select a date and time to filter by availability.'}
          </p>
        </div>

        {/* Filter bar */}
        <div className="mb-6 rounded-xl border border-neutral-200 bg-white p-4 shadow-sm">
          {/* Row 1: date + time window + attendees */}
          <div className="flex flex-wrap gap-3 items-end">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">Date</label>
              <input
                type="date"
                value={date}
                min={today}
                onChange={e => setDate(e.target.value)}
                className="h-9 rounded-md border border-neutral-300 px-2 text-sm text-neutral-900 focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">Start time</label>
              <select
                value={startTime}
                onChange={e => setStartTime(e.target.value)}
                className="h-9 rounded-md border border-neutral-300 px-2 text-sm text-neutral-900 focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              >
                <option value="">-- start --</option>
                {TIME_OPTIONS.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">End time</label>
              <select
                value={endTime}
                onChange={e => setEndTime(e.target.value)}
                className="h-9 rounded-md border border-neutral-300 px-2 text-sm text-neutral-900 focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              >
                <option value="">-- end --</option>
                {TIME_OPTIONS.filter(t => !startTime || t > startTime).map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">Min. attendees</label>
              <input
                type="number"
                min={1}
                value={attendees}
                onChange={e => setAttendees(e.target.value)}
                placeholder="Any"
                className="h-9 w-24 rounded-md border border-neutral-300 px-2 text-sm text-neutral-900 focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              />
            </div>
          </div>

          {/* Row 2: location filters */}
          <div className="mt-3 flex flex-wrap gap-3 items-end">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">Campus</label>
              <input
                type="text"
                value={campus}
                onChange={e => setCampus(e.target.value)}
                placeholder="All campuses"
                className="h-9 rounded-md border border-neutral-300 px-2 text-sm text-neutral-900 focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">Building</label>
              <input
                type="text"
                value={building}
                onChange={e => setBuilding(e.target.value)}
                placeholder="All buildings"
                className="h-9 rounded-md border border-neutral-300 px-2 text-sm text-neutral-900 focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">Floor</label>
              <input
                type="text"
                value={floor}
                onChange={e => setFloor(e.target.value)}
                placeholder="All floors"
                className="h-9 w-28 rounded-md border border-neutral-300 px-2 text-sm text-neutral-900 focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">Area</label>
              <input
                type="text"
                value={area}
                onChange={e => setArea(e.target.value)}
                placeholder="All areas"
                className="h-9 rounded-md border border-neutral-300 px-2 text-sm text-neutral-900 focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">Room type</label>
              <select
                value={roomType}
                onChange={e => setRoomType(e.target.value)}
                className="h-9 rounded-md border border-neutral-300 px-2 text-sm text-neutral-900 focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              >
                <option value="">All types</option>
                {ROOM_TYPES.map(t => (
                  <option key={t} value={t}>{ROOM_TYPE_LABELS[t]}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Row 3: equipment checkboxes */}
          <div className="mt-3">
            <p className="mb-1.5 text-xs font-medium text-neutral-600">Equipment</p>
            <div className="flex flex-wrap gap-2">
              {EQUIPMENT_OPTIONS.map(eq => {
                const Icon = EQUIPMENT_ICONS[eq]
                const checked = selectedEquipment.includes(eq)
                return (
                  <label
                    key={eq}
                    className={cn(
                      'inline-flex cursor-pointer items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium select-none transition-colors',
                      checked
                        ? 'border-[#085E5E] bg-[#085E5E]/10 text-[#085E5E]'
                        : 'border-neutral-200 bg-white text-neutral-600 hover:border-neutral-300',
                    )}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={checked}
                      onChange={() => toggleEquipment(eq)}
                    />
                    <Icon className="h-3.5 w-3.5" />
                    {EQUIPMENT_LABELS[eq]}
                  </label>
                )
              })}
            </div>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {error instanceof Error ? error.message : 'Failed to load rooms.'}
          </div>
        )}

        {/* Results */}
        {isLoading ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <RoomCardSkeleton key={i} />
            ))}
          </div>
        ) : rooms.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-neutral-200 bg-white py-16 text-center">
            <Users className="mb-3 h-10 w-10 text-neutral-300" />
            <p className="text-base font-medium text-neutral-500">No rooms found</p>
            <p className="mt-1 text-sm text-neutral-400">
              {hasWindow
                ? 'No rooms are available for the selected time window. Try adjusting the filters.'
                : 'No rooms match the current filters. Try broadening your search.'}
            </p>
          </div>
        ) : (
          <>
            <p className="mb-3 text-sm text-neutral-500">
              {rooms.length} room{rooms.length !== 1 ? 's' : ''} found
              {hasWindow ? ' — available for selected window' : ''}
            </p>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {rooms.map(room => (
                <RoomCard key={room.id} room={room} onBook={handleBook} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
