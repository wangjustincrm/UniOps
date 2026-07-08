/**
 * RoomDetailPage — /rooms/:id
 *
 * Shows room header, equipment, DayTimeline + WeekStrip, and "Book this room" button.
 */
import { useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft, Users, Clock, Wrench, AlertCircle,
  Tv, Presentation, PenLine, Video, Phone,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useRoomDetail } from '@/services/api'
import type { EquipmentOption, RoomStatus } from '@/lib/types'
import { EQUIPMENT_LABELS } from '@/lib/types'
import { ROOM_STATUS_STYLE, ROOM_STATUS_LABEL } from '@/lib/roomStatus'
import { DayTimeline } from '@/components/DayTimeline'
import { WeekStrip } from '@/components/WeekStrip'

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
    <div className="animate-pulse space-y-4 rounded-xl border border-neutral-200 bg-white p-6">
      <div className="h-7 w-1/2 rounded bg-neutral-200" />
      <div className="h-4 w-1/3 rounded bg-neutral-100" />
      <div className="h-4 w-1/4 rounded bg-neutral-100" />
      <div className="mt-6 h-8 w-full rounded bg-neutral-200" />
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RoomDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  const today = new Date().toISOString().slice(0, 10)
  const [selectedDay, setSelectedDay] = useState<string>(today)

  const { data: room, isLoading, error } = useRoomDetail(id)

  if (isLoading) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6">
          <button
            type="button"
            onClick={() => navigate('/rooms')}
            className="mb-4 flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 transition-colors"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Rooms
          </button>
          <Skeleton />
        </div>
      </div>
    )
  }

  if (error || !room) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6">
          <button
            type="button"
            onClick={() => navigate('/rooms')}
            className="mb-4 flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Rooms
          </button>
          <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {error instanceof Error ? error.message : 'Room not found.'}
          </div>
        </div>
      </div>
    )
  }

  const isBookable = room.status === 'available' as RoomStatus
  const locationParts = [room.campus, room.building, room.floor, room.area].filter(Boolean)

  const nextTime = room.next_meeting_at
    ? new Date(room.next_meeting_at).toLocaleTimeString('en-CA', {
        hour: '2-digit', minute: '2-digit', hour12: false,
      })
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
        <button
          type="button"
          onClick={() => navigate('/rooms')}
          className="flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Rooms
        </button>

        {/* Room header card */}
        <div className="rounded-xl border border-neutral-200 bg-white p-6 shadow-sm">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="min-w-0">
              <div className="flex items-center gap-3 flex-wrap">
                <h1 className="text-2xl font-bold text-neutral-900">{room.name}</h1>
                <span className="font-mono text-sm text-neutral-400 bg-neutral-50 border border-neutral-200 rounded px-2 py-0.5">
                  {room.code}
                </span>
              </div>
              {locationParts.length > 0 && (
                <p className="mt-1 text-sm text-neutral-500">{locationParts.join(' · ')}</p>
              )}
            </div>
            {/* Status badge */}
            <span className={cn(
              'inline-flex items-center rounded-full px-3 py-1 text-sm font-medium ring-1 ring-inset',
              ROOM_STATUS_STYLE[room.status_now],
            )}>
              {ROOM_STATUS_LABEL[room.status_now]}
            </span>
          </div>

          {/* Capacity + next meeting */}
          <div className="mt-4 flex flex-wrap gap-4">
            <div className="flex items-center gap-1.5 text-sm text-neutral-600">
              <Users className="h-4 w-4 text-neutral-400" />
              Capacity: {room.capacity}
            </div>
            {nextTime && (
              <div className="flex items-center gap-1.5 text-sm text-neutral-500">
                <Clock className="h-4 w-4 text-neutral-400" />
                Next meeting: {nextTime}
              </div>
            )}
            {room.open_time_start && room.open_time_end && (
              <div className="flex items-center gap-1.5 text-sm text-neutral-500">
                <Clock className="h-4 w-4 text-neutral-400" />
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
                    className="inline-flex items-center gap-1.5 rounded-md bg-neutral-50 border border-neutral-200 px-2 py-1 text-xs text-neutral-600"
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
            <div className="mt-4 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
              <Wrench className="h-4 w-4 shrink-0 mt-0.5" />
              <span>{room.notes}</span>
            </div>
          )}

          {/* Image thumbnails — only when image_file_ids provided and FILE_API configured */}
          {room.image_file_ids.length > 0 && (() => {
            const fileBase = (import.meta.env.VITE_FILE_API_URL as string | undefined) ?? ''
            if (!fileBase) return null
            return (
              <div className="mt-4 flex gap-2 flex-wrap">
                {room.image_file_ids.map((fid) => (
                  <img
                    key={fid}
                    src={`${fileBase}/files/v1/files/${fid}`}
                    alt={room.name}
                    className="h-20 w-28 rounded-lg object-cover border border-neutral-200"
                    onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                  />
                ))}
              </div>
            )
          })()}

          {/* Book button */}
          <div className="mt-6">
            <button
              type="button"
              onClick={handleBook}
              disabled={!isBookable}
              className={cn(
                'rounded-lg px-5 py-2.5 text-sm font-semibold transition-colors',
                isBookable
                  ? 'bg-[#085E5E] text-white hover:bg-[#074f4f]'
                  : 'bg-neutral-100 text-neutral-400 cursor-not-allowed',
              )}
            >
              {isBookable ? 'Book this room' : 'Room unavailable for booking'}
            </button>
          </div>
        </div>

        {/* Timeline card */}
        <div className="rounded-xl border border-neutral-200 bg-white p-6 shadow-sm space-y-4">
          <h2 className="text-base font-semibold text-neutral-800">Schedule</h2>

          {/* Week strip */}
          <WeekStrip
            weekBookings={room.week_bookings}
            selectedDay={selectedDay}
            onSelectDay={setSelectedDay}
          />

          {/* Day timeline for selected day */}
          <div className="mt-3">
            <p className="mb-2 text-xs font-medium text-neutral-500">
              {new Date(`${selectedDay}T12:00:00`).toLocaleDateString('en-CA', {
                weekday: 'long', month: 'long', day: 'numeric',
              })}
            </p>
            <DayTimeline
              openStart={room.open_time_start}
              openEnd={room.open_time_end}
              bookings={selectedDay === today ? room.today_bookings : room.week_bookings}
              day={selectedDay}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
