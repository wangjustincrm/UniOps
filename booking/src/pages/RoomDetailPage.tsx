/**
 * RoomDetailPage — /rooms/:id
 *
 * Shows room header, equipment, DayTimeline + WeekStrip, and "Book this room" button.
 */
import { useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import {
  ArrowLeft, Users, Clock, Wrench, AlertCircle,
  Tv, Presentation, PenLine, Video, Phone,
  type LucideIcon,
} from 'lucide-react'
import { formatDate, formatTime, todayISO } from '@/lib/utils'
import { useRoomDetail } from '@/services/api'
import type { EquipmentOption, RoomStatus } from '@/lib/types'
import { EQUIPMENT_LABELS } from '@/lib/types'
import { ROOM_STATUS_TONE, ROOM_STATUS_LABEL } from '@/lib/roomStatus'
import { DayTimeline } from '@/components/DayTimeline'
import { WeekStrip } from '@/components/WeekStrip'
import { AuthImage } from '@/components/AuthImage'
import { Button, Badge, Card } from '@uniops/shell'

// ── Equipment icon map ────────────────────────────────────────────────────────

const EQUIPMENT_ICONS: Record<EquipmentOption, LucideIcon> = {
  tv:         Tv,
  projector:  Presentation,
  whiteboard: PenLine,
  video_conf: Video,
  phone_conf: Phone,
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function Skeleton() {
  return (
    <Card className="animate-pulse space-y-4 p-6">
      <div className="h-7 w-1/2 rounded bg-surface-strong" />
      <div className="h-4 w-1/3 rounded bg-surface-sunken" />
      <div className="h-4 w-1/4 rounded bg-surface-sunken" />
      <div className="mt-6 h-8 w-full rounded bg-surface-strong" />
    </Card>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RoomDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  const today = todayISO()
  const [selectedDay, setSelectedDay] = useState<string>(today)

  const { data: room, isLoading, error } = useRoomDetail(id)
  useDocTabTitle(room?.name)

  if (isLoading) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6">
          <BackLink to="/rooms"
            className="mb-4 flex items-center gap-1.5 text-sm text-fg-muted hover:text-fg-secondary transition-colors"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Rooms
          </BackLink>
          <Skeleton />
        </div>
      </div>
    )
  }

  if (error || !room) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6">
          <BackLink to="/rooms"
            className="mb-4 flex items-center gap-1.5 text-sm text-fg-muted hover:text-fg-secondary"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Rooms
          </BackLink>
          <div className="flex items-center gap-2 rounded-md border border-danger-line bg-danger-soft px-4 py-3 text-sm text-danger-fg">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {error instanceof Error ? error.message : 'Room not found.'}
          </div>
        </div>
      </div>
    )
  }

  const isBookable = room.status === 'available' as RoomStatus
  const locationParts = [room.floor, room.area].filter(Boolean)

  const nextTime = room.next_meeting_at
    ? formatTime(room.next_meeting_at)
    : null

  function handleBook() {
    // Build query params — carry existing start/end if present, plus selected day
    const params = new URLSearchParams()
    const start = searchParams.get('start')
    const end = searchParams.get('end')
    if (start) params.set('start', start)
    if (end) params.set('end', end)
    const qs = params.toString()
    navigate(`/rooms/${room!.id}/book${qs ? '?' + qs : ''}`)
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6 space-y-6">
        {/* Back nav */}
        <BackLink to="/rooms"
          className="flex items-center gap-1.5 text-sm text-fg-muted hover:text-fg-secondary transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Rooms
        </BackLink>

        {/* Room header card */}
        <Card className="p-6">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="min-w-0">
              <div className="flex items-center gap-3 flex-wrap">
                <h1 className="text-2xl font-semibold text-fg">{room.name}</h1>
                <Badge variant="outline" className="font-mono">
                  {room.code}
                </Badge>
              </div>
              {locationParts.length > 0 && (
                <p className="mt-1 text-sm text-fg-muted">{locationParts.join(' · ')}</p>
              )}
            </div>
            {/* Status badge */}
            <Badge variant={ROOM_STATUS_TONE[room.status_now]}>
              {ROOM_STATUS_LABEL[room.status_now]}
            </Badge>
          </div>

          {/* Capacity + next meeting */}
          <div className="mt-4 flex flex-wrap gap-4">
            <div className="flex items-center gap-1.5 text-sm text-fg-secondary">
              <Users className="h-4 w-4 text-fg-muted" />
              Capacity: {room.capacity}
            </div>
            {nextTime && (
              <div className="flex items-center gap-1.5 text-sm text-fg-muted">
                <Clock className="h-4 w-4 text-fg-muted" />
                Next meeting: {nextTime}
              </div>
            )}
            {room.open_time_start && room.open_time_end && (
              <div className="flex items-center gap-1.5 text-sm text-fg-muted">
                <Clock className="h-4 w-4 text-fg-muted" />
                Open: {room.open_time_start.slice(0, 5)}–{room.open_time_end.slice(0, 5)}
              </div>
            )}
          </div>

          {/* Equipment */}
          {room.equipment.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {room.equipment.map((eq) => {
                const Icon = EQUIPMENT_ICONS[eq as EquipmentOption]
                const label = EQUIPMENT_LABELS[eq as EquipmentOption] ?? eq
                return (
                  <span
                    key={eq}
                    className="inline-flex items-center gap-1.5 rounded-md bg-surface-subtle border border-line px-2 py-1 text-xs text-fg-secondary"
                  >
                    {Icon && <Icon className="h-3.5 w-3.5" />}
                    {label}
                  </span>
                )
              })}
            </div>
          )}

          {/* Maintenance notes */}
          {room.status === 'maintenance' && room.notes && (
            <div className="mt-4 flex items-start gap-2 rounded-md border border-warning-line bg-warning-soft px-3 py-2 text-sm text-warning-fg">
              <Wrench className="h-4 w-4 shrink-0 mt-0.5" />
              <span>{room.notes}</span>
            </div>
          )}

          {/* Image thumbnails — authenticated via AuthImage (file-api requires Bearer token) */}
          {room.image_file_ids.length > 0 && (() => {
            const fileBase = (import.meta.env.VITE_FILE_API_URL as string | undefined) ?? ''
            if (!fileBase) return null
            return (
              <div className="mt-4 flex gap-2 flex-wrap">
                {room.image_file_ids.map((fid) => (
                  <AuthImage
                    key={fid}
                    src={`${fileBase}/files/v1/files/${fid}`}
                    alt={room.name}
                    className="h-20 w-28 rounded-lg object-cover border border-line"
                  />
                ))}
              </div>
            )
          })()}

          {/* Book button */}
          <div className="mt-6">
            <Button
              size="lg"
              onClick={handleBook}
              disabled={!isBookable}
            >
              {isBookable ? 'Book this room' : 'Room unavailable for booking'}
            </Button>
          </div>
        </Card>

        {/* Timeline card */}
        <Card className="p-6 space-y-4">
          <h2 className="text-base font-semibold text-fg">Schedule</h2>

          {/* Week strip */}
          <WeekStrip
            weekBookings={room.week_bookings}
            selectedDay={selectedDay}
            onSelectDay={setSelectedDay}
          />

          {/* Day timeline for selected day */}
          <div className="mt-3">
            <p className="mb-2 text-xs font-medium text-fg-muted">
              {new Date(`${selectedDay}T12:00:00`).toLocaleDateString('en-CA', { weekday: 'long' })}, {formatDate(selectedDay)}
            </p>
            <DayTimeline
              openStart={room.open_time_start}
              openEnd={room.open_time_end}
              bookings={selectedDay === today ? room.today_bookings : room.week_bookings}
              day={selectedDay}
            />
          </div>
        </Card>
      </div>
    </div>
  )
}
