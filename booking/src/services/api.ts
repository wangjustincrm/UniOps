/**
 * Typed service layer for booking-api.
 *
 * Mirror shape: vms/src/services/api.ts
 *
 * NOTE: the `api` client in src/lib/api.ts does NOT auto-prefix /api/v1.
 * All paths here must include /api/v1 explicitly.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, getToken } from '@/lib/api'
import type {
  AdminBookingFilters,
  AdminBookingListOut,
  AdminConfig,
  BookingAdminOut,
  BookingOut,
  BookingSlimOut,
  DirectoryUserOut,
  ImportResult,
  NotificationListOut,
  NotificationLogOut,
  RoomCreate,
  RoomDetailOut,
  RoomOut,
  RoomUpdate,
  RoomWithStatusOut,
  SeriesSpec,
  StatusChangeOut,
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
    return api.get<RoomDetailOut>(`/api/v1/rooms/${id}`)
  },
}

// ── React Query hooks for rooms ───────────────────────────────────────────────

export function useRoomList(filters: RoomListFilters = {}, enabled = true) {
  return useQuery<RoomWithStatusOut[]>({
    queryKey: ['booking-rooms', filters],
    queryFn: () => roomService.list(filters),
    enabled,
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
  return useQuery<RoomDetailOut>({
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
  occurrence_conflicts: Array<{ date: string; conflicts: BookingSlimOut[] }>
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

// ── File-api image upload ─────────────────────────────────────────────────────

const FILE_API_BASE = (import.meta.env.VITE_FILE_API_URL as string | undefined) ?? ''

/**
 * Upload a single image to file-api and return the file id (UUID string).
 *
 * Contract: POST /files/v1/files?doc_type=room_image&doc_id=<uuid>
 * Both query params are required (FastAPI Query(...)). Response field for the
 * file identifier is `id` (UUID, JSON-serialised as a string).
 * See file-api/app/api/v1/files.py lines 38–88 and schemas/file.py.
 *
 * @param file     The image File to upload.
 * @param docId    The room UUID to associate with this file. For new rooms that
 *                 do not have a DB id yet, pass a client-generated UUID (stable
 *                 per modal session so all images share the same doc_id).
 */
export async function uploadRoomImage(file: File, docId: string): Promise<string> {
  if (!FILE_API_BASE) {
    throw new Error('File API URL not configured (VITE_FILE_API_URL is unset).')
  }
  const token = getToken()
  const fd = new FormData()
  fd.append('file', file)
  const qs = new URLSearchParams({ doc_type: 'room_image', doc_id: docId }).toString()
  const resp = await fetch(`${FILE_API_BASE}/files/v1/files?${qs}`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail ?? `Upload failed: HTTP ${resp.status}`)
  }
  const data: { id: string } = await resp.json()
  return data.id
}

// ── Admin — helpers ───────────────────────────────────────────────────────────

const BASE_URL = (import.meta.env.VITE_API_URL as string | undefined) || ''

/** Multipart POST (file upload) with auth header */
async function postMultipart<T>(path: string, formData: FormData): Promise<T> {
  const token = getToken()
  const resp = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail ?? `HTTP ${resp.status}`)
  }
  return resp.json()
}

/** Authenticated fetch returning a blob (for CSV export) */
async function fetchBlob(path: string): Promise<Blob> {
  const token = getToken()
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail ?? `HTTP ${resp.status}`)
  }
  return resp.blob()
}

// ── Admin — Room service ──────────────────────────────────────────────────────

export const adminRoomService = {
  list(filters: { floor?: string; area?: string; status?: string } = {}) {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(filters)) {
      if (v) qs.set(k, v)
    }
    const q = qs.toString()
    return api.get<RoomOut[]>(`/api/v1/admin/rooms${q ? '?' + q : ''}`)
  },

  create(payload: RoomCreate) {
    return api.post<RoomOut>('/api/v1/admin/rooms', payload)
  },

  update(id: string, payload: RoomUpdate) {
    return api.patch<RoomOut>(`/api/v1/admin/rooms/${id}`, payload)
  },

  setStatus(id: string, status: string, notes?: string) {
    return api.post<StatusChangeOut>(`/api/v1/admin/rooms/${id}/status`, { status, notes })
  },

  importXlsx(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return postMultipart<ImportResult>('/api/v1/admin/rooms/import', fd)
  },
}

export function useAdminRoomList(
  filters: { floor?: string; area?: string; status?: string } = {},
) {
  return useQuery<RoomOut[]>({
    queryKey: ['admin-rooms', filters],
    queryFn: () => adminRoomService.list(filters),
    staleTime: 30_000,
  })
}

export function useAdminCreateRoom() {
  const qc = useQueryClient()
  return useMutation<RoomOut, Error, RoomCreate>({
    mutationFn: (payload) => adminRoomService.create(payload),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-rooms'] }) },
  })
}

export function useAdminUpdateRoom(id: string) {
  const qc = useQueryClient()
  return useMutation<RoomOut, Error, RoomUpdate>({
    mutationFn: (payload) => adminRoomService.update(id, payload),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-rooms'] }) },
  })
}

export function useAdminSetRoomStatus(id: string) {
  const qc = useQueryClient()
  return useMutation<StatusChangeOut, Error, { status: string; notes?: string }>({
    mutationFn: ({ status, notes }) => adminRoomService.setStatus(id, status, notes),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-rooms'] }) },
  })
}

export function useAdminImportRooms() {
  const qc = useQueryClient()
  return useMutation<ImportResult, Error, File>({
    mutationFn: (file) => adminRoomService.importXlsx(file),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-rooms'] }) },
  })
}

// ── Admin — Config service ────────────────────────────────────────────────────

export const adminConfigService = {
  get() {
    return api.get<AdminConfig>('/api/v1/admin/config')
  },
  update(payload: Partial<AdminConfig>) {
    return api.put<AdminConfig>('/api/v1/admin/config', payload)
  },
}

export function useAdminConfig() {
  return useQuery<AdminConfig>({
    queryKey: ['admin-config'],
    queryFn: () => adminConfigService.get(),
    staleTime: 60_000,
  })
}

export function useAdminUpdateConfig() {
  const qc = useQueryClient()
  return useMutation<AdminConfig, Error, Partial<AdminConfig>>({
    mutationFn: (payload) => adminConfigService.update(payload),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-config'] }) },
  })
}

// ── Admin — Bookings service ──────────────────────────────────────────────────

export const adminBookingService = {
  list(filters: AdminBookingFilters = {}) {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(filters)) {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
    }
    const q = qs.toString()
    return api.get<AdminBookingListOut>(`/api/v1/admin/bookings${q ? '?' + q : ''}`)
  },

  async exportCsv(filters: Omit<AdminBookingFilters, 'limit' | 'offset'> = {}): Promise<void> {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(filters)) {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
    }
    const q = qs.toString()
    const blob = await fetchBlob(`/api/v1/admin/bookings/export${q ? '?' + q : ''}`)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `bookings-export-${new Date().toISOString().slice(0, 10)}.csv`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 100)
  },

  forceCancel(id: string, series = false) {
    const qs = series ? '?series=true' : ''
    return api.post<{ cancelled: number }>(`/api/v1/bookings/${id}/cancel${qs}`, {})
  },
}

export function useAdminBookings(filters: AdminBookingFilters = {}) {
  return useQuery<AdminBookingListOut>({
    queryKey: ['admin-bookings', filters],
    queryFn: () => adminBookingService.list(filters),
    staleTime: 30_000,
  })
}

export function useAdminForceCancel() {
  const qc = useQueryClient()
  return useMutation<{ cancelled: number }, Error, { id: string; series?: boolean }>({
    mutationFn: ({ id, series }) => adminBookingService.forceCancel(id, series),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-bookings'] }) },
  })
}

// ── Admin — Notifications service ─────────────────────────────────────────────

export const adminNotificationService = {
  list(filters: { status?: string; notif_type?: string; limit?: number; offset?: number } = {}) {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(filters)) {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
    }
    const q = qs.toString()
    return api.get<NotificationListOut>(`/api/v1/admin/notifications${q ? '?' + q : ''}`)
  },

  resend(id: string) {
    return api.post<NotificationLogOut>(`/api/v1/admin/notifications/${id}/resend`, {})
  },
}

export function useAdminNotifications(
  filters: { status?: string; notif_type?: string; limit?: number; offset?: number } = {},
) {
  return useQuery<NotificationListOut>({
    queryKey: ['admin-notifications', filters],
    queryFn: () => adminNotificationService.list(filters),
    staleTime: 30_000,
  })
}

export function useAdminResendNotification() {
  const qc = useQueryClient()
  return useMutation<NotificationLogOut, Error, string>({
    mutationFn: (id) => adminNotificationService.resend(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-notifications'] }) },
  })
}

// Re-export types consumed by admin pages (convenience)
export type { RoomOut, RoomCreate, RoomUpdate, StatusChangeOut, AdminConfig, BookingAdminOut, NotificationLogOut, AdminBookingFilters }
