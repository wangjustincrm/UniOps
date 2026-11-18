import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Users, Clock, AlertCircle,
  Tv, Presentation, PenLine, Video, Phone,
  type LucideIcon,
} from 'lucide-react'
import { cn, formatTime, todayISO } from '@/lib/utils'
import { useRoomList, useRoomAvailability, type RoomListFilters } from '@/services/api'
import type { RoomWithStatusOut, RoomStatusNow, EquipmentOption } from '@/lib/types'
import { EQUIPMENT_OPTIONS, EQUIPMENT_LABELS, ROOM_TYPES, ROOM_TYPE_LABELS } from '@/lib/types'
import { ROOM_STATUS_TONE, ROOM_STATUS_LABEL } from '@/lib/roomStatus'
import { zonedToISO } from '@/lib/tz'

import { Button, Checkbox, Input, Select, Badge, Card } from '@uniops/shell'
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
    <Badge variant={ROOM_STATUS_TONE[status]}>
      {ROOM_STATUS_LABEL[status]}
    </Badge>
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

/** Combine a YYYY-MM-DD date string and HH:MM wall-clock time (plant time zone) into a UTC ISO string. */
const toISO = (date: string, time: string) => zonedToISO(date, time)

// ── Skeleton loader ───────────────────────────────────────────────────────────

function RoomCardSkeleton() {
  return (
    <Card className="animate-pulse p-4 flex flex-col gap-3">
      <div className="h-4 w-3/4 rounded bg-surface-strong" />
      <div className="h-3 w-1/2 rounded bg-surface-sunken" />
      <div className="h-3 w-1/3 rounded bg-surface-sunken" />
      <div className="flex gap-2 mt-auto">
        <div className="h-8 w-16 rounded bg-surface-sunken" />
        <div className="ml-auto h-8 w-20 rounded bg-surface-strong" />
      </div>
    </Card>
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
  const locationParts = [room.floor, room.area].filter(Boolean)
  const locationLine = locationParts.length > 0 ? locationParts.join(' · ') : null

  const nextTime = room.next_meeting_at
    ? formatTime(room.next_meeting_at)
    : null

  const isBookable = room.status === 'available'

  return (
    <div className="flex flex-col rounded-xl border border-line bg-surface p-4 gap-3 shadow-sm hover:shadow-md transition-shadow">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-fg truncate">{room.name}</p>
          <p className="text-xs text-fg-muted font-mono mt-0.5">{room.code}</p>
        </div>
        <RoomStatusBadge status={room.status_now} />
      </div>

      {/* Location */}
      {locationLine && (
        <p className="text-sm text-fg-muted truncate">{locationLine}</p>
      )}

      {/* Capacity */}
      <div className="flex items-center gap-1.5 text-sm text-fg-secondary">
        <Users className="h-3.5 w-3.5 shrink-0 text-fg-muted" />
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
              <Badge variant="outline" key={eq} title={label}>
                <Icon className="h-3 w-3" />
                {label}
              </Badge>
            )
          })}
        </div>
      )}

      {/* Next meeting */}
      {nextTime && (
        <div className="flex items-center gap-1.5 text-xs text-fg-muted">
          <Clock className="h-3 w-3" />
          Next meeting {nextTime}
        </div>
      )}

      {/* Actions */}
      <div className="mt-auto pt-2">
        <Button
          size="md"
          className="w-full"
          onClick={() => onBook(room)}
          disabled={!isBookable}
        >
          {isBookable ? 'Book' : 'Unavailable'}
        </Button>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RoomsPage() {
  const navigate = useNavigate()
  const today = todayISO()

  // Filter state
  const [date, setDate] = useState<string>(today)
  const [startTime, setStartTime] = useState<string>('')
  const [endTime, setEndTime] = useState<string>('')
  const [attendees, setAttendees] = useState<string>('')
  const [floor, setFloor] = useState<string>('')
  const [area, setArea] = useState<string>('')
  const [selectedEquipment, setSelectedEquipment] = useState<string[]>([])
  const [roomType, setRoomType] = useState<string>('')

  // Build filter object for the service calls
  const filters: RoomListFilters = useMemo(() => {
    const f: RoomListFilters = {}
    if (floor.trim())     f.floor    = floor.trim()
    if (area.trim())      f.area     = area.trim()
    if (attendees && Number(attendees) > 0) f.min_capacity = Number(attendees)
    if (selectedEquipment.length > 0) f.equipment = selectedEquipment
    if (roomType) f.room_type = roomType
    return f
  }, [floor, area, attendees, selectedEquipment, roomType])

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
          <h1 className="text-2xl font-semibold text-fg">Meeting Rooms</h1>
          <p className="mt-1 text-sm text-fg-muted">
            {hasWindow
              ? 'Showing available rooms for the selected time window.'
              : 'Showing all rooms with live status. Select a date and time to filter by availability.'}
          </p>
        </div>

        {/* Filter bar */}
        <Card className="mb-6 p-4">
          {/* Row 1: date + time window + attendees + location + type */}
          <div className="flex flex-wrap gap-3 items-end">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-fg-secondary">Date</label>
              <Input inputSize="sm"
                type="date"
                value={date}
                min={today}
                onChange={e => setDate(e.target.value)}
               
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-fg-secondary">Start time</label>
              <Select selectSize="sm"
                value={startTime}
                onChange={e => setStartTime(e.target.value)}
               
              >
                <option value="">-- start --</option>
                {TIME_OPTIONS.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </Select>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-fg-secondary">End time</label>
              <Select selectSize="sm"
                value={endTime}
                onChange={e => setEndTime(e.target.value)}
               
              >
                <option value="">-- end --</option>
                {TIME_OPTIONS.filter(t => !startTime || t > startTime).map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </Select>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-fg-secondary">Min. attendees</label>
              <Input inputSize="sm"
                type="number"
                min={1}
                value={attendees}
                onChange={e => setAttendees(e.target.value)}
                placeholder="Any"
                className="w-24"
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-fg-secondary">Floor</label>
              <Input inputSize="sm"
                type="text"
                value={floor}
                onChange={e => setFloor(e.target.value)}
                placeholder="All floors"
                className="w-28"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-fg-secondary">Area</label>
              <Input inputSize="sm"
                type="text"
                value={area}
                onChange={e => setArea(e.target.value)}
                placeholder="All areas"
               
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-fg-secondary">Room type</label>
              <Select selectSize="sm"
                value={roomType}
                onChange={e => setRoomType(e.target.value)}
               
              >
                <option value="">All types</option>
                {ROOM_TYPES.map(t => (
                  <option key={t} value={t}>{ROOM_TYPE_LABELS[t]}</option>
                ))}
              </Select>
            </div>
          </div>

          {/* Row 2: equipment checkboxes */}
          <div className="mt-3">
            <p className="mb-1.5 text-xs font-medium text-fg-secondary">Equipment</p>
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
                        ? 'border-primary-700 bg-primary-700/10 text-primary-700'
                        : 'border-line bg-surface text-fg-secondary hover:border-line-strong',
                    )}
                  >
                    <Checkbox
                     
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
        </Card>

        {/* Error */}
        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
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
          <Card className="flex flex-col items-center justify-center py-16 text-center">
            <Users className="mb-3 h-10 w-10 text-fg-faint" />
            <p className="text-base font-medium text-fg-muted">No rooms found</p>
            <p className="mt-1 text-sm text-fg-muted">
              {hasWindow
                ? 'No rooms are available for the selected time window. Try adjusting the filters.'
                : 'No rooms match the current filters. Try broadening your search.'}
            </p>
          </Card>
        ) : (
          <>
            <p className="mb-3 text-sm text-fg-muted">
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
