/**
 * TypeScript mirrors of booking-api Pydantic schemas.
 * Keep in sync with booking-api/app/schemas/*.py
 */

// ── Room ──────────────────────────────────────────────────────────────────────

/** status_now values returned by GET /api/v1/rooms and /api/v1/rooms/availability */
export type RoomStatusNow = 'free' | 'in_use' | 'starting_soon' | 'booked' | 'disabled' | 'maintenance'

/** Admin-level room status (availability of the physical room resource) */
export type RoomStatus = 'available' | 'disabled' | 'maintenance'

export interface RoomWithStatusOut {
  id: string
  name: string
  code: string
  campus: string | null
  building: string | null
  floor: string | null
  area: string | null
  capacity: number
  equipment: string[]
  room_type: string
  open_time_start: string | null  // "HH:MM:SS" local-time string
  open_time_end: string | null
  advance_booking_days: number | null
  status: RoomStatus
  owner_department: string | null
  notes: string | null
  image_file_ids: string[]
  created_at: string
  updated_at: string
  // real-time computed fields
  status_now: RoomStatusNow
  next_meeting_at: string | null
}

/** Room detail with today's and this week's bookings */
export interface RoomDetailOut extends RoomWithStatusOut {
  today_bookings: BookingSlimOut[]
  week_bookings: BookingSlimOut[]
}

// ── Booking ───────────────────────────────────────────────────────────────────

export interface BookingSlimOut {
  id: string
  title: string
  starts_at: string
  ends_at: string
  organizer_name: string
}

export interface BookingOut extends BookingSlimOut {
  description: string | null
  attendee_ids: string[]
  room_id: string
  room_name: string
  room_code: string
  status: string
  series_id: string | null
  rrule: string | null
  sync_status: string
  ical_sequence: number
}

// ── Suggestions ───────────────────────────────────────────────────────────────

export interface SuggestOut {
  nearest_slots: { starts_at: string; ends_at: string }[]
  alternative_rooms: RoomWithStatusOut[]
}

// ── Series ────────────────────────────────────────────────────────────────────

export interface SeriesSpec {
  freq: 'daily' | 'weekly'
  interval: number
  count?: number | null
  until?: string | null
}

// ── Directory ─────────────────────────────────────────────────────────────────

export interface DirectoryUserOut {
  id: string
  full_name: string
  email: string
}

// ── Constants ─────────────────────────────────────────────────────────────────

export const EQUIPMENT_OPTIONS = [
  'tv',
  'projector',
  'whiteboard',
  'video_conf',
  'phone_conf',
] as const

export type EquipmentOption = (typeof EQUIPMENT_OPTIONS)[number]

export const EQUIPMENT_LABELS: Record<EquipmentOption, string> = {
  tv: 'TV',
  projector: 'Projector',
  whiteboard: 'Whiteboard',
  video_conf: 'Video Conference',
  phone_conf: 'Phone Conference',
}

export const ROOM_TYPES = [
  'standard',
  'training',
  'boardroom',
  'multi_function',
] as const

export type RoomType = (typeof ROOM_TYPES)[number]

export const ROOM_TYPE_LABELS: Record<RoomType, string> = {
  standard: 'Standard',
  training: 'Training Room',
  boardroom: 'Boardroom',
  multi_function: 'Multi-Function',
}
