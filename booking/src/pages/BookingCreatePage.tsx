/**
 * BookingCreatePage — /rooms/:id/book
 *
 * Form: title, description, date+time, attendees, video conf, recurrence.
 * Live precheck (debounced 400ms) → green "Available" or SuggestionPanel.
 * Submit → 201 success toast + navigate /my | 400 conflict SuggestionPanel | 422 error.
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate, useSearchParams, useLocation } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, AlertCircle, Loader2, Info } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ApiError } from '@/lib/api'
import { useRoomDetail, usePrecheckBooking, useCreateBooking } from '@/services/api'
import type { DirectoryUserOut, SeriesSpec } from '@/lib/types'
import type { PrecheckOut } from '@/services/api'
import { AttendeePicker } from '@/components/AttendeePicker'
import { RecurrencePicker } from '@/components/RecurrencePicker'
import { SuggestionPanel } from '@/components/SuggestionPanel'
import type { RoomWithStatusOut } from '@/lib/types'
import { useBookingAuth } from '@/store/auth'

// ── Time options (15-min steps, 06:00–23:00) ──────────────────────────────────

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

/** Date+time → UTC ISO string */
function toISO(date: string, time: string): string {
  return new Date(`${date}T${time}:00`).toISOString()
}

/** UTC ISO → local "HH:MM" */
function isoToHM(iso: string): string {
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** UTC ISO → local "YYYY-MM-DD" */
function isoToDate(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// ── Toast (simple inline) ─────────────────────────────────────────────────────

interface ToastState { message: string; type: 'success' | 'error' }

// ── Form state from location (room swap preserving form state) ────────────────

interface FormState {
  title?: string
  description?: string
  date?: string
  startTime?: string
  endTime?: string
  attendees?: DirectoryUserOut[]
  needsVideoConf?: boolean
  series?: SeriesSpec | null
}

// ── Form field helpers ────────────────────────────────────────────────────────

const inputCls =
  'w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm ' +
  'outline-none focus:border-[#085E5E]/60 focus:ring-1 focus:ring-[#085E5E]/30'

function Field({ label, required, children, hint }: {
  label: string
  required?: boolean
  children: React.ReactNode
  hint?: string
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-neutral-700">
        {label}{required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      <div className="mt-1">{children}</div>
      {hint && <p className="mt-1 text-xs text-neutral-500">{hint}</p>}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">{title}</h2>
      <div className="space-y-4">{children}</div>
    </section>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function BookingCreatePage() {
  const { id: roomId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const location = useLocation()

  // Current user id — used to exclude organizer from the attendee picker
  const currentUserId = useBookingAuth((s) => s.user?.id)

  // Restore form state when navigating from "pick alternative room"
  const locationState = (location.state as FormState | null) ?? {}

  const { data: room, isLoading: roomLoading } = useRoomDetail(roomId)

  // ── Form state ──────────────────────────────────────────────────────────────
  const today = new Date().toISOString().slice(0, 10)

  // Prefill from query params (from RoomsPage/RoomDetailPage) or location state
  const startParam = searchParams.get('start')
  const endParam   = searchParams.get('end')

  const [title, setTitle]       = useState(locationState.title ?? '')
  const [description, setDesc]  = useState(locationState.description ?? '')
  const [date, setDate]         = useState(() => {
    if (locationState.date) return locationState.date
    if (startParam) return isoToDate(startParam)
    return today
  })
  const [startTime, setStart]   = useState(() => {
    if (locationState.startTime) return locationState.startTime
    if (startParam) return isoToHM(startParam)
    return '09:00'
  })
  const [endTime, setEnd]       = useState(() => {
    if (locationState.endTime) return locationState.endTime
    if (endParam) return isoToHM(endParam)
    return '10:00'
  })
  const [attendees, setAttendees]   = useState<DirectoryUserOut[]>(locationState.attendees ?? [])
  const [needsVideo, setNeedsVideo] = useState(locationState.needsVideoConf ?? false)
  const [series, setSeries]         = useState<SeriesSpec | null>(locationState.series ?? null)

  // B5: When the same component instance is kept alive (tab already open) and a new
  // room or location.state arrives (e.g. user picked an alternative room from a conflict
  // flow), apply the carried form snapshot explicitly — useState initialisers don't rerun.
  const prevRoomIdRef = useRef<string | undefined>(undefined)
  const prevStateRef  = useRef<FormState>({})
  useEffect(() => {
    const stateChanged = location.state !== null && location.state !== prevStateRef.current
    const roomChanged  = roomId !== prevRoomIdRef.current
    if ((stateChanged || roomChanged) && location.state) {
      const s = location.state as FormState
      if (s.title        !== undefined) setTitle(s.title)
      if (s.description  !== undefined) setDesc(s.description)
      if (s.date         !== undefined) setDate(s.date)
      if (s.startTime    !== undefined) setStart(s.startTime)
      if (s.endTime      !== undefined) setEnd(s.endTime)
      if (s.attendees    !== undefined) setAttendees(s.attendees)
      if (s.needsVideoConf !== undefined) setNeedsVideo(s.needsVideoConf)
      if (s.series       !== undefined) setSeries(s.series)
    }
    prevRoomIdRef.current  = roomId
    prevStateRef.current   = (location.state as FormState) ?? {}
  }, [roomId, location.state])

  // ── Precheck ────────────────────────────────────────────────────────────────
  const [precheckResult, setPrecheckResult] = useState<PrecheckOut | null>(null)
  const [precheckAvailable, setPrecheckAvailable] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // B4: Sequence counter to discard stale out-of-order precheck responses
  const precheckSeqRef = useRef(0)

  const precheck = usePrecheckBooking()
  const create   = useCreateBooking()

  // ── Toast ───────────────────────────────────────────────────────────────────
  const [toast, setToast] = useState<ToastState | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [conflictResult, setConflictResult] = useState<PrecheckOut | null>(null)
  const [truncatedNotice, setTruncatedNotice] = useState(false)

  const hasWindow = date && startTime && endTime && startTime < endTime

  // Debounced precheck trigger
  const runPrecheck = useCallback(() => {
    if (!roomId || !hasWindow) {
      setPrecheckResult(null)
      setPrecheckAvailable(false)
      return
    }
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      // B4: capture sequence number before the async call; discard results from stale calls
      const seq = ++precheckSeqRef.current
      precheck.mutate(
        {
          room_id: roomId,
          starts_at: toISO(date, startTime),
          ends_at: toISO(date, endTime),
          attendee_count: attendees.length > 0 ? attendees.length : undefined,
          series: series ?? undefined,
        },
        {
          onSuccess: (data) => {
            if (seq !== precheckSeqRef.current) return  // discard stale response
            setPrecheckResult(data)
            const hasConflict = data.conflicts.length > 0 || data.occurrence_conflicts.length > 0
            setPrecheckAvailable(!hasConflict)
          },
          onError: () => {
            if (seq !== precheckSeqRef.current) return  // discard stale response
            setPrecheckResult(null)
            setPrecheckAvailable(false)
          },
        },
      )
    }, 400)
  }, [roomId, date, startTime, endTime, attendees.length, series, hasWindow, precheck])

  // Trigger precheck on relevant field changes
  useEffect(() => {
    runPrecheck()
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, startTime, endTime, series, roomId])

  // ── Video conf guard ────────────────────────────────────────────────────────
  const roomHasVideo = room?.equipment.includes('video_conf') ?? false
  // If needs_video_conf is checked but room lacks it → disable submit
  const videoConflict = needsVideo && !roomHasVideo

  // ── Submit ──────────────────────────────────────────────────────────────────
  const canSubmit = !!(
    title.trim() &&
    hasWindow &&
    roomId &&
    !videoConflict &&
    !create.isPending
  )

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit || !roomId) return
    setFormError(null)
    setConflictResult(null)
    setTruncatedNotice(false)

    create.mutate(
      {
        room_id: roomId,
        title: title.trim(),
        description: description.trim() || null,
        attendee_ids: attendees.map((u) => u.id),
        starts_at: toISO(date, startTime),
        ends_at: toISO(date, endTime),
        needs_video_conf: needsVideo,
        series: series ?? null,
      },
      {
        onSuccess: (data) => {
          if (data.series_truncated) setTruncatedNotice(true)
          setToast({ message: `Booked! ${data.bookings.length} booking${data.bookings.length !== 1 ? 's' : ''} created.`, type: 'success' })
          setTimeout(() => navigate('/my'), 1500)
        },
        onError: (err) => {
          // B3: structural check — any 400 with a conflicts array is a booking conflict,
          // regardless of the exact detail string the server sends.
          if (
            err instanceof ApiError &&
            err.status === 400 &&
            Array.isArray((err.body as any)?.conflicts)
          ) {
            setConflictResult(err.body as PrecheckOut)
          } else {
            setFormError((err as Error).message)
          }
        },
      },
    )
  }

  // ── Suggestion handlers ─────────────────────────────────────────────────────

  function handlePickSlot(startsISO: string, endsISO: string) {
    setDate(isoToDate(startsISO))
    setStart(isoToHM(startsISO))
    setEnd(isoToHM(endsISO))
    setPrecheckResult(null)
    setConflictResult(null)
  }

  function handlePickRoom(altRoom: RoomWithStatusOut) {
    // Navigate to that room's booking page, preserving form state
    const state: FormState = {
      title, description, date, startTime, endTime, attendees, needsVideoConf: needsVideo, series,
    }
    navigate(`/rooms/${altRoom.id}/book`, { state })
  }

  // ── Derived display state ───────────────────────────────────────────────────
  const activePrecheckResult = conflictResult ?? precheckResult
  const showConflict = activePrecheckResult &&
    (activePrecheckResult.conflicts.length > 0 || activePrecheckResult.occurrence_conflicts.length > 0)

  // ── Render: loading / error for room ───────────────────────────────────────
  if (roomLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-neutral-500">
        <Loader2 className="h-4 w-4 animate-spin mr-2" />
        Loading room…
      </div>
    )
  }

  if (!room) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-2xl px-4 py-6">
          <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
            <AlertCircle className="h-4 w-4" />
            Room not found.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-4 py-6 sm:px-6 space-y-6">
        {/* Back nav */}
        <button
          type="button"
          onClick={() => navigate(`/rooms/${roomId}`)}
          className="flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to {room.name}
        </button>

        {/* Toast */}
        {toast && (
          <div className={cn(
            'flex items-center gap-2 rounded-md border px-4 py-3 text-sm',
            toast.type === 'success'
              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
              : 'border-red-200 bg-red-50 text-red-600',
          )}>
            {toast.type === 'success'
              ? <CheckCircle2 className="h-4 w-4 shrink-0" />
              : <AlertCircle className="h-4 w-4 shrink-0" />}
            {toast.message}
          </div>
        )}

        {/* Truncated series notice */}
        {truncatedNotice && (
          <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
            <Info className="h-4 w-4 shrink-0 mt-0.5" />
            Series shortened to fit the booking window.
          </div>
        )}

        {/* Page title */}
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Book {room.name}</h1>
          <p className="mt-1 text-sm text-neutral-500">
            {[room.floor, room.area].filter(Boolean).join(' · ')}
            {room.capacity > 0 && ` · Capacity: ${room.capacity}`}
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="rounded-xl border border-neutral-200 bg-white p-6 shadow-sm space-y-6">
          <Section title="Details">
            <Field label="Title" required>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Meeting title…"
                className={inputCls}
                autoFocus
              />
            </Field>

            <Field label="Description">
              <textarea
                value={description}
                onChange={(e) => setDesc(e.target.value)}
                rows={3}
                placeholder="Optional agenda or notes…"
                className={cn(inputCls, 'resize-y')}
              />
            </Field>
          </Section>

          <Section title="Time">
            <Field label="Date" required>
              <input
                type="date"
                value={date}
                min={today}
                onChange={(e) => setDate(e.target.value)}
                className={inputCls}
              />
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="Start time" required>
                <select
                  value={startTime}
                  onChange={(e) => setStart(e.target.value)}
                  className={inputCls}
                >
                  {TIME_OPTIONS.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </Field>
              <Field label="End time" required>
                <select
                  value={endTime}
                  onChange={(e) => setEnd(e.target.value)}
                  className={inputCls}
                >
                  {TIME_OPTIONS.filter((t) => t > startTime).map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </Field>
            </div>
          </Section>

          {/* Live precheck status */}
          {hasWindow && (
            <div>
              {precheck.isPending && (
                <div className="flex items-center gap-2 text-xs text-neutral-500">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Checking availability…
                </div>
              )}
              {!precheck.isPending && precheckAvailable && !conflictResult && (
                <div className="flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
                  <CheckCircle2 className="h-4 w-4 shrink-0" />
                  Room is available for the selected time.
                </div>
              )}
              {!precheck.isPending && showConflict && (
                <SuggestionPanel
                  conflicts={activePrecheckResult!.conflicts}
                  occurrenceConflicts={activePrecheckResult!.occurrence_conflicts}
                  suggestions={activePrecheckResult!.suggestions}
                  onPickSlot={handlePickSlot}
                  onPickRoom={handlePickRoom}
                />
              )}
            </div>
          )}

          <Section title="Attendees">
            {/* B6: exclude organizer (current user) — they are auto-included server-side */}
            <AttendeePicker value={attendees} onChange={setAttendees} excludeUserId={currentUserId} />
          </Section>

          <Section title="Options">
            {/* Video conference */}
            <label className="flex items-start gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={needsVideo}
                disabled={!roomHasVideo && !needsVideo}
                onChange={(e) => setNeedsVideo(e.target.checked)}
                className="mt-0.5 accent-[#085E5E] h-4 w-4 rounded"
              />
              <span>
                <span className={cn('text-sm font-medium', !roomHasVideo ? 'text-neutral-400' : 'text-neutral-700')}>
                  Needs video conference
                </span>
                {!roomHasVideo && (
                  <span className="ml-2 text-xs text-neutral-400">(room has no video equipment)</span>
                )}
              </span>
            </label>
            {videoConflict && (
              <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
                <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                This room does not have video conferencing. Please uncheck or choose a room with video equipment.
              </div>
            )}
          </Section>

          <Section title="Recurrence">
            <RecurrencePicker value={series} onChange={setSeries} />
          </Section>

          {/* Form-level error */}
          {formError && (
            <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {formError}
            </div>
          )}

          {/* Submit */}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => navigate(`/rooms/${roomId}`)}
              className="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-md px-4 py-2 text-sm font-semibold transition-colors',
                canSubmit
                  ? 'bg-[#085E5E] text-white hover:bg-[#074f4f]'
                  : 'bg-neutral-100 text-neutral-400 cursor-not-allowed',
              )}
            >
              {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              {series ? 'Book Series' : 'Book Room'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
