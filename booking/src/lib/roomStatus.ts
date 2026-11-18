/**
 * Shared status badge styles and labels for RoomStatusNow.
 * Single source of truth — import in SuggestionPanel, RoomDetailPage, RoomsPage.
 */
import type { RoomStatusNow } from '@/lib/types'

export const ROOM_STATUS_STYLE: Record<RoomStatusNow, string> = {
  free:          'bg-success-soft text-success-fg ring-success-line',
  in_use:        'bg-danger-soft text-danger-fg ring-danger-line',
  starting_soon: 'bg-warning-soft text-warning-fg ring-warning-line',
  booked:        'bg-info-soft text-info-fg ring-info-line',
  disabled:      'bg-surface-sunken text-fg-muted ring-line',
  maintenance:   'bg-surface-sunken text-fg-muted ring-line',
}

export const ROOM_STATUS_LABEL: Record<RoomStatusNow, string> = {
  free:          'Available',
  in_use:        'In Use',
  starting_soon: 'Starting Soon',
  booked:        'Booked',
  disabled:      'Disabled',
  maintenance:   'Maintenance',
}
