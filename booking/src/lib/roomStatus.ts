/**
 * Shared status badge styles and labels for RoomStatusNow.
 * Single source of truth — import in SuggestionPanel, RoomDetailPage, RoomsPage.
 */
import type { RoomStatusNow } from '@/lib/types'

export const ROOM_STATUS_STYLE: Record<RoomStatusNow, string> = {
  free:          'bg-emerald-50 text-emerald-700 ring-emerald-200',
  in_use:        'bg-red-50 text-red-700 ring-red-200',
  starting_soon: 'bg-amber-50 text-amber-700 ring-amber-200',
  booked:        'bg-blue-50 text-blue-700 ring-blue-200',
  disabled:      'bg-neutral-100 text-neutral-500 ring-neutral-200',
  maintenance:   'bg-neutral-100 text-neutral-500 ring-neutral-200',
}

export const ROOM_STATUS_LABEL: Record<RoomStatusNow, string> = {
  free:          'Available',
  in_use:        'In Use',
  starting_soon: 'Starting Soon',
  booked:        'Booked',
  disabled:      'Disabled',
  maintenance:   'Maintenance',
}
