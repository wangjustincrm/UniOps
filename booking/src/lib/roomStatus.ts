/**
 * Shared status badge styles and labels for RoomStatusNow.
 * Single source of truth — import in SuggestionPanel, RoomDetailPage, RoomsPage.
 */
import type { BadgeTone } from '@uniops/shell'
import type { RoomStatusNow } from '@/lib/types'

export const ROOM_STATUS_TONE: Record<RoomStatusNow, BadgeTone> = {
  free:          'success',
  in_use:        'danger',
  starting_soon: 'warning',
  booked:        'blue',
  disabled:      'neutral',
  maintenance:   'neutral',
}

export const ROOM_STATUS_LABEL: Record<RoomStatusNow, string> = {
  free:          'Available',
  in_use:        'In Use',
  starting_soon: 'Starting Soon',
  booked:        'Booked',
  disabled:      'Disabled',
  maintenance:   'Maintenance',
}
