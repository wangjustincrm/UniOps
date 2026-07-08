/**
 * SuggestionPanel — renders precheck/create conflict results + suggestions.
 *
 * - conflicts: who/when has a conflict for single-booking
 * - occurrenceConflicts: per-occurrence conflicts for recurrences
 * - suggestions.nearest_slots: clickable time buttons
 * - suggestions.alternative_rooms: clickable room cards
 */
import { Clock, MapPin, Users, Video, CheckCircle2, AlertTriangle } from 'lucide-react'
import type { BookingSlimOut, SuggestOut, RoomWithStatusOut } from '@/lib/types'
import { cn } from '@/lib/utils'
import { ROOM_STATUS_STYLE, ROOM_STATUS_LABEL } from '@/lib/roomStatus'

interface Props {
  conflicts: BookingSlimOut[]
  occurrenceConflicts: Array<{ date: string; conflicts: BookingSlimOut[] }>
  suggestions: SuggestOut | null
  onPickSlot: (startsISO: string, endsISO: string) => void
  onPickRoom: (room: RoomWithStatusOut) => void
}

function formatSlot(starts: string, ends: string): string {
  const fmt = (iso: string) =>
    new Date(iso).toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit', hour12: false })
  return `${fmt(starts)}–${fmt(ends)}`
}

function formatDateTime(iso: string): string {
  const d = new Date(iso)
  const date = d.toLocaleDateString('en-CA', { month: 'short', day: 'numeric' })
  const time = d.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit', hour12: false })
  return `${date} ${time}`
}

export function SuggestionPanel({ conflicts, occurrenceConflicts, suggestions, onPickSlot, onPickRoom }: Props) {
  const hasConflicts = conflicts.length > 0 || occurrenceConflicts.length > 0

  if (!hasConflicts && !suggestions) return null

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 space-y-4">
      {/* Single-booking conflicts */}
      {conflicts.length > 0 && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
            <span className="text-sm font-semibold text-amber-800">
              {conflicts.length} conflict{conflicts.length !== 1 ? 's' : ''}
            </span>
          </div>
          <ul className="space-y-1">
            {conflicts.map((c) => (
              <li key={c.id} className="flex items-start gap-2 text-xs text-amber-700">
                <Clock className="h-3.5 w-3.5 shrink-0 mt-0.5 text-amber-500" />
                <span>
                  <span className="font-medium">{c.title}</span>
                  {' · '}
                  {formatSlot(c.starts_at, c.ends_at)}
                  {' · '}
                  {c.organizer_name}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Recurrence occurrence conflicts */}
      {occurrenceConflicts.length > 0 && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
            <span className="text-sm font-semibold text-amber-800">
              {occurrenceConflicts.length} occurrence{occurrenceConflicts.length !== 1 ? 's' : ''} with conflicts
            </span>
          </div>
          <ul className="space-y-2">
            {occurrenceConflicts.map((oc) => (
              <li key={oc.date} className="text-xs text-amber-700">
                <p className="font-medium mb-0.5">{oc.date}</p>
                <ul className="ml-3 space-y-0.5">
                  {oc.conflicts.map((c) => (
                    <li key={c.id} className="flex items-start gap-1.5">
                      <Clock className="h-3 w-3 shrink-0 mt-0.5 text-amber-500" />
                      <span>
                        {c.title}
                        {' · '}
                        {formatDateTime(c.starts_at)}–{new Date(c.ends_at).toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit', hour12: false })}
                      </span>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Nearest slots */}
      {(suggestions?.nearest_slots?.length ?? 0) > 0 && (
        <div>
          <p className="text-xs font-semibold text-neutral-600 mb-2">Nearest available slots:</p>
          <div className="flex flex-wrap gap-2">
            {suggestions!.nearest_slots.map((slot, i) => (
              <button
                key={i}
                type="button"
                onClick={() => onPickSlot(slot.starts_at, slot.ends_at)}
                className={cn(
                  'inline-flex items-center gap-1 rounded-md border border-[#085E5E] bg-white px-2.5 py-1',
                  'text-xs font-medium text-[#085E5E] hover:bg-[#085E5E]/5 transition-colors',
                )}
              >
                <CheckCircle2 className="h-3 w-3" />
                {formatSlot(slot.starts_at, slot.ends_at)}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Alternative rooms */}
      {(suggestions?.alternative_rooms?.length ?? 0) > 0 && (
        <div>
          <p className="text-xs font-semibold text-neutral-600 mb-2">Alternative rooms:</p>
          <div className="flex flex-col gap-2">
            {suggestions!.alternative_rooms.map((room) => {
              const locationParts = [room.floor, room.area].filter(Boolean)
              return (
                <button
                  key={room.id}
                  type="button"
                  onClick={() => onPickRoom(room)}
                  className={cn(
                    'flex items-center gap-3 rounded-lg border border-neutral-200 bg-white p-3 text-left',
                    'hover:border-[#085E5E]/40 hover:bg-[#085E5E]/5 transition-colors',
                  )}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-neutral-900 truncate">{room.name}</p>
                    <div className="flex items-center gap-3 mt-0.5">
                      {locationParts.length > 0 && (
                        <span className="flex items-center gap-1 text-xs text-neutral-500">
                          <MapPin className="h-3 w-3" />
                          {locationParts.join(' · ')}
                        </span>
                      )}
                      <span className="flex items-center gap-1 text-xs text-neutral-500">
                        <Users className="h-3 w-3" />
                        {room.capacity}
                      </span>
                      {room.equipment.includes('video_conf') && (
                        <span className="flex items-center gap-1 text-xs text-neutral-500">
                          <Video className="h-3 w-3" />
                          Video
                        </span>
                      )}
                    </div>
                  </div>
                  <span className={cn(
                    'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset shrink-0',
                    ROOM_STATUS_STYLE[room.status_now],
                  )}>
                    {ROOM_STATUS_LABEL[room.status_now]}
                  </span>
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
