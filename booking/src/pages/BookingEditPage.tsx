/**
 * BookingEditPage — /my/:id/edit
 *
 * Reuses the same form pieces as BookingCreatePage (time selects, AttendeePicker,
 * SuggestionPanel) but NO RecurrencePicker — series editing is blocked upstream.
 *
 * Prefill: prefers booking passed via location.state; falls back to fetching
 * /my list and finding by id. Not-found → back to /my.
 *
 * Room switch: native <select> populated from roomService.list().
 *
 * Live precheck: same 400ms debounce as create, but filters out a conflict that
 * is EXACTLY the booking being edited (precheck has no exclude param; compare ids).
 *
 * Submit: PATCH /bookings/:id  → success → /my with note; 400 conflict → SuggestionPanel.
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ApiError } from '@/lib/api'
import {
  useMyBookings,
  useUpdateBooking,
  usePrecheckBooking,
  useRoomList,
} from '@/services/api'
import type { BookingOut, DirectoryUserOut, RoomWithStatusOut } from '@/lib/types'
import type { PrecheckOut } from '@/services/api'
import { AttendeePicker } from '@/components/AttendeePicker'
import { SuggestionPanel } from '@/components/SuggestionPanel'
import { useBookingAuth } from '@/store/auth'

// ── Time helpers (duplicated from CreatePage to keep pages independent) ────────

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

function toISO(date: string, time: string): string {
  return new Date(`${date}T${time}:00`).toISOString()
}

function isoToHM(iso: string): string {
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function isoToDate(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// ── Form UI helpers (mirrors BookingCreatePage style) ─────────────────────────

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

interface LocationState {
  booking?: BookingOut
  successNote?: string
}

export default function BookingEditPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()

  const currentUserId = useBookingAuth((s) => s.user?.id)

  // ── Resolve booking: prefer location.state, fall back to mine list ──────────
  const locationState = (location.state as LocationState | null) ?? {}
  const [booking, setBooking] = useState<BookingOut | null>(locationState.booking ?? null)
  const [notFound, setNotFound] = useState(false)

  const { data: mineList } = useMyBookings()

  useEffect(() => {
    if (booking) return   // already have it from location.state
    if (!mineList) return
    const found = mineList.find((b) => b.id === id)
    if (found) {
      setBooking(found)
    } else {
      setNotFound(true)
    }
  }, [mineList, id, booking])

  // ── Room list ────────────────────────────────────────────────────────────────
  const { data: rooms } = useRoomList()

  // ── Form state (initialised when booking resolves) ───────────────────────────
  const [title, setTitle]             = useState('')
  const [description, setDesc]        = useState('')
  const [date, setDate]               = useState('')
  const [startTime, setStart]         = useState('09:00')
  const [endTime, setEnd]             = useState('10:00')
  const [roomId, setRoomId]           = useState('')
  const [attendees, setAttendees]     = useState<DirectoryUserOut[]>([])
  const [attendeesTouched, setAttendeesTouched] = useState(false)
  const [formInitialised, setFormInit] = useState(false)

  // Prefill attendees: we only have ids from BookingOut — resolve to DirectoryUserOut
  // via the existing mine data (we don't have a separate user-detail endpoint).
  // We can construct minimal DirectoryUserOut objects from the booking data we have.
  // The AttendeePicker only needs id+full_name+email for display/chip rendering.
  // Since we only have attendee_ids (not names), we leave attendees empty on first
  // load; the user can re-add. This is consistent with the brief (no attendee-detail
  // endpoint mentioned). We do prefill all other fields.
  useEffect(() => {
    if (!booking || formInitialised) return
    setTitle(booking.title)
    setDesc(booking.description ?? '')
    setDate(isoToDate(booking.starts_at))
    setStart(isoToHM(booking.starts_at))
    setEnd(isoToHM(booking.ends_at))
    setRoomId(booking.room_id)
    // attendees: we have ids but no names — initialise to empty; user re-adds if needed
    setAttendees([])
    setAttendeesTouched(false)
    setFormInit(true)
  }, [booking, formInitialised])

  // ── Precheck ─────────────────────────────────────────────────────────────────
  const [precheckResult, setPrecheckResult] = useState<PrecheckOut | null>(null)
  const [precheckAvailable, setPrecheckAvailable] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const precheckSeqRef = useRef(0)

  const precheck = usePrecheckBooking()

  const hasWindow = !!(date && startTime && endTime && startTime < endTime)

  const runPrecheck = useCallback(() => {
    if (!roomId || !hasWindow) {
      setPrecheckResult(null)
      setPrecheckAvailable(false)
      return
    }
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      const seq = ++precheckSeqRef.current
      precheck.mutate(
        {
          room_id: roomId,
          starts_at: toISO(date, startTime),
          ends_at: toISO(date, endTime),
          attendee_count: attendees.length > 0 ? attendees.length : undefined,
        },
        {
          onSuccess: (data) => {
            if (seq !== precheckSeqRef.current) return
            // Filter out conflicts that are THIS booking (self-conflict from precheck)
            const filteredConflicts = data.conflicts.filter((c) => c.id !== id)
            const filteredOccurrence = data.occurrence_conflicts.map((occ) => ({
              ...occ,
              conflicts: occ.conflicts.filter((c) => c.id !== id),
            })).filter((occ) => occ.conflicts.length > 0)
            const filtered: PrecheckOut = {
              ...data,
              conflicts: filteredConflicts,
              occurrence_conflicts: filteredOccurrence,
            }
            setPrecheckResult(filtered)
            const hasConflict =
              filteredConflicts.length > 0 || filteredOccurrence.length > 0
            setPrecheckAvailable(!hasConflict)
          },
          onError: () => {
            if (seq !== precheckSeqRef.current) return
            setPrecheckResult(null)
            setPrecheckAvailable(false)
          },
        },
      )
    }, 400)
  }, [roomId, date, startTime, endTime, attendees.length, hasWindow, id, precheck])

  useEffect(() => {
    if (!formInitialised) return
    runPrecheck()
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, startTime, endTime, roomId, formInitialised])

  // ── Submit / conflict state ───────────────────────────────────────────────────
  const [formError, setFormError]       = useState<string | null>(null)
  const [conflictResult, setConflictResult] = useState<PrecheckOut | null>(null)

  const update = useUpdateBooking(id)

  // Block submit only when precheck has run and explicitly found a conflict (not merely "not yet checked")
  const precheckHasConflict =
    precheckResult !== null &&
    (precheckResult.conflicts.length > 0 || precheckResult.occurrence_conflicts.length > 0)

  const canSubmit = !!(
    title.trim() &&
    hasWindow &&
    roomId &&
    !update.isPending &&
    !precheckHasConflict
  )

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setFormError(null)
    setConflictResult(null)

    update.mutate(
      {
        title: title.trim(),
        description: description.trim() || null,
        // Only include attendee_ids if the user has explicitly changed the attendee list.
        // If untouched, omit the field so the server preserves existing attendees.
        ...(attendeesTouched ? { attendee_ids: attendees.map((u) => u.id) } : {}),
        room_id: roomId,
        starts_at: toISO(date, startTime),
        ends_at: toISO(date, endTime),
      },
      {
        onSuccess: () => {
          navigate('/my', { state: { successNote: 'Booking updated.' } })
        },
        onError: (err) => {
          if (
            err instanceof ApiError &&
            err.status === 400 &&
            Array.isArray((err.body as Record<string, unknown>)?.conflicts)
          ) {
            setConflictResult(err.body as PrecheckOut)
          } else if (
            err instanceof ApiError &&
            err.status === 400 &&
            (err.body as Record<string, unknown>)?.detail === 'series_member_immutable'
          ) {
            setFormError('Recurring meeting occurrences cannot be edited individually.')
          } else {
            setFormError((err as Error).message)
          }
        },
      },
    )
  }

  // Suggestion panel handlers (for PATCH conflict flow)
  function handlePickSlot(startsISO: string, endsISO: string) {
    setDate(isoToDate(startsISO))
    setStart(isoToHM(startsISO))
    setEnd(isoToHM(endsISO))
    setPrecheckResult(null)
    setConflictResult(null)
  }

  function handlePickRoom(altRoom: RoomWithStatusOut) {
    setRoomId(altRoom.id)
    setConflictResult(null)
    setPrecheckResult(null)
  }

  const activePrecheckResult = conflictResult ?? precheckResult
  const showConflict =
    activePrecheckResult &&
    (activePrecheckResult.conflicts.length > 0 || activePrecheckResult.occurrence_conflicts.length > 0)

  // ── Render: not found ────────────────────────────────────────────────────────
  if (notFound) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-2xl px-4 py-6">
          <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
            <AlertCircle className="h-4 w-4" />
            Booking not found.
          </div>
          <button
            type="button"
            onClick={() => navigate('/my')}
            className="mt-4 flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to My Bookings
          </button>
        </div>
      </div>
    )
  }

  // ── Render: loading ──────────────────────────────────────────────────────────
  if (!booking || !formInitialised) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-neutral-500">
        <Loader2 className="h-4 w-4 animate-spin mr-2" />
        Loading booking…
      </div>
    )
  }

  const today = new Date().toISOString().slice(0, 10)

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-4 py-6 sm:px-6 space-y-6">
        {/* Back nav */}
        <button
          type="button"
          onClick={() => navigate('/my')}
          className="flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to My Bookings
        </button>

        {/* Page title */}
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Edit Booking</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Update details, time, or room. Attendees will receive an updated invite.
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

          <Section title="Room">
            <Field label="Room" required>
              <select
                value={roomId}
                onChange={(e) => setRoomId(e.target.value)}
                className={inputCls}
              >
                {/* Keep current room as fallback option even if rooms list hasn't loaded */}
                {!rooms && (
                  <option value={booking.room_id}>
                    {booking.room_name} ({booking.room_code})
                  </option>
                )}
                {rooms?.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name} ({r.code})
                  </option>
                ))}
              </select>
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

          {/* Live precheck */}
          {hasWindow && formInitialised && (
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
            <Field
              label="Additional attendees"
              hint={
                booking.attendee_ids.length > 0
                  ? `Previously: ${booking.attendee_ids.length} attendee(s). Leave untouched to keep them, or re-add to replace the full list.`
                  : undefined
              }
            >
              <AttendeePicker
                value={attendees}
                onChange={(val) => { setAttendeesTouched(true); setAttendees(val) }}
                excludeUserId={currentUserId}
              />
            </Field>
          </Section>

          {/* Form error */}
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
              onClick={() => navigate('/my')}
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
              {update.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Save Changes
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
