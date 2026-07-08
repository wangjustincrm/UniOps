/**
 * Typed service layer for booking-api.
 *
 * Mirror shape: vms/src/services/api.ts
 *
 * NOTE: the `api` client in src/lib/api.ts does NOT auto-prefix /api/v1.
 * All paths here must include /api/v1 explicitly.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type {
  BookingOut,
  BookingSlimOut,
  DirectoryUserOut,
  RoomWithStatusOut,
  SeriesSpec,
  SuggestOut,
} from '@/lib/types'

// ── Filter shapes ─────────────────────────────────────────────────────────────

export interface RoomListFilters {
  campus?: string
  building?: string
  floor?: string
  area?: string
  min_capacity?: number
  equipment?: string[]
  room_type?: string
}

export interface RoomAvailabilityFilters extends RoomListFilters {
  starts_at: string  // ISO datetime
  ends_at: string    // ISO datetime
}

// ── Room service ──────────────────────────────────────────────────────────────

function buildRoomQs(filters: RoomListFilters): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v === undefined || v === null || v === '') continue
    if (k === 'equipment' && Array.isArray(v)) {
      for (const item of v) qs.append('equipment', item)
    } else {
      qs.set(k, String(v))
    }
  }
  return qs.toString()
}

export const roomService = {
  list(filters: RoomListFilters = {}) {
    const qs = buildRoomQs(filters)
    return api.get<RoomWithStatusOut[]>(`/api/v1/rooms${qs ? '?' + qs : ''}`)
  },

  availability(window: { starts_at: string; ends_at: string }, filters: RoomListFilters = {}) {
    const qs = buildRoomQs({ ...filters, ...window })
    return api.get<RoomWithStatusOut[]>(`/api/v1/rooms/availability${qs ? '?' + qs : ''}`)
  },

  detail(id: string) {
    return api.get<RoomWithStatusOut & {
      today_bookings: BookingSlimOut[]
      week_bookings: BookingSlimOut[]
    }>(`/api/v1/rooms/${id}`)
  },
}

// ── React Query hooks for rooms ───────────────────────────────────────────────

export function useRoomList(filters: RoomListFilters = {}) {
  return useQuery<RoomWithStatusOut[]>({
    queryKey: ['booking-rooms', filters],
    queryFn: () => roomService.list(filters),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
}

export function useRoomAvailability(
  window: { starts_at: string; ends_at: string } | null,
  filters: RoomListFilters = {},
) {
  return useQuery<RoomWithStatusOut[]>({
    queryKey: ['booking-rooms-availability', window, filters],
    queryFn: () => roomService.availability(window!, filters),
    enabled: window !== null,
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
}

export function useRoomDetail(id: string | undefined) {
  return useQuery<RoomWithStatusOut & {
    today_bookings: BookingSlimOut[]
    week_bookings: BookingSlimOut[]
  }>({
    queryKey: ['booking-room', id],
    queryFn: () => roomService.detail(id!),
    enabled: !!id,
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
}

// ── Directory service ─────────────────────────────────────────────────────────

export const directoryService = {
  search(q: string) {
    const qs = new URLSearchParams()
    if (q) qs.set('q', q)
    return api.get<DirectoryUserOut[]>(`/api/v1/users/directory${qs.toString() ? '?' + qs.toString() : ''}`)
  },
}

export function useDirectorySearch(q: string) {
  return useQuery<DirectoryUserOut[]>({
    queryKey: ['booking-directory', q],
    queryFn: () => directoryService.search(q),
    enabled: q.length >= 2,
    staleTime: 30_000,
  })
}

// ── Booking create / precheck shapes ─────────────────────────────────────────

export interface BookingCreatePayload {
  room_id: string
  title: string
  description?: string | null
  attendee_ids?: string[]
  starts_at: string
  ends_at: string
  needs_video_conf?: boolean
  series?: SeriesSpec | null
}

export interface BookingUpdatePayload {
  title?: string | null
  description?: string | null
  attendee_ids?: string[] | null
  room_id?: string | null
  starts_at?: string | null
  ends_at?: string | null
}

export interface PrecheckIn {
  room_id: string
  starts_at: string
  ends_at: string
  attendee_count?: number | null
  equipment?: string[]
  series?: SeriesSpec | null
}

export interface PrecheckOut {
  conflicts: BookingSlimOut[]
  occurrence_conflicts: unknown[]
  suggestions: SuggestOut | null
}

export interface BookingCreatedOut {
  bookings: BookingOut[]
  series_id: string | null
  series_truncated: boolean
}

// ── Booking service (thin wrappers — consumed by Tasks 14/15) ─────────────────

export const bookingService = {
  precheck(payload: PrecheckIn) {
    return api.post<PrecheckOut>('/api/v1/bookings/precheck', payload)
  },

  create(payload: BookingCreatePayload) {
    return api.post<BookingCreatedOut>('/api/v1/bookings', payload)
  },

  mine(limit = 200, offset = 0) {
    const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    return api.get<BookingOut[]>(`/api/v1/bookings/mine?${qs.toString()}`)
  },

  update(id: string, payload: BookingUpdatePayload) {
    return api.patch<BookingOut>(`/api/v1/bookings/${id}`, payload)
  },

  cancel(id: string, series = false) {
    const qs = series ? '?series=true' : ''
    return api.post<{ cancelled: number }>(`/api/v1/bookings/${id}/cancel${qs}`, {})
  },
}

// ── React Query hooks for bookings ────────────────────────────────────────────

export function useMyBookings(limit = 200, offset = 0) {
  return useQuery<BookingOut[]>({
    queryKey: ['booking-mine', limit, offset],
    queryFn: () => bookingService.mine(limit, offset),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
}

export function useCreateBooking() {
  const qc = useQueryClient()
  return useMutation<BookingCreatedOut, Error, BookingCreatePayload>({
    mutationFn: (payload) => bookingService.create(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['booking-mine'] })
      qc.invalidateQueries({ queryKey: ['booking-rooms'] })
      qc.invalidateQueries({ queryKey: ['booking-rooms-availability'] })
    },
  })
}

export function useUpdateBooking(id: string | undefined) {
  const qc = useQueryClient()
  return useMutation<BookingOut, Error, BookingUpdatePayload>({
    mutationFn: (payload) => bookingService.update(id!, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['booking-mine'] })
      qc.invalidateQueries({ queryKey: ['booking-room', id] })
    },
  })
}

export function useCancelBooking() {
  const qc = useQueryClient()
  return useMutation<{ cancelled: number }, Error, { id: string; series?: boolean }>({
    mutationFn: ({ id, series }) => bookingService.cancel(id, series),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['booking-mine'] })
      qc.invalidateQueries({ queryKey: ['booking-rooms'] })
      qc.invalidateQueries({ queryKey: ['booking-rooms-availability'] })
    },
  })
}

export function usePrecheckBooking() {
  return useMutation<PrecheckOut, Error, PrecheckIn>({
    mutationFn: (payload) => bookingService.precheck(payload),
  })
}
