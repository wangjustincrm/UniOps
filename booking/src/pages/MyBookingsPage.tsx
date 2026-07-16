/**
 * MyBookingsPage — /my
 *
 * Two tabs:
 *   - Upcoming: ends_at >= now AND status === 'confirmed'
 *   - Past:     everything else (including cancelled — labelled distinctly)
 *
 * Row columns: title, room (name + code), date + time range, attendee count,
 * status badge, sync_status badge (with tooltip), series icon.
 *
 * Row actions (Upcoming + confirmed only):
 *   - Edit: disabled for series members (tooltip); else navigate /my/:id/edit
 *   - Cancel: confirm dialog; series members → ?series=true
 */
import { useState, useMemo, useEffect, useRef } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  CalendarDays,
  Users,
  Repeat,
  Loader2,
  AlertCircle,
  Pencil,
  X as XIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMyBookings, useCancelBooking } from '@/services/api'
import type { BookingOut } from '@/lib/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-CA', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-CA', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

// ── Status badges ─────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const confirmed = status === 'confirmed'
  const cancelled = status === 'cancelled'
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        confirmed && 'bg-emerald-50 text-emerald-700 ring-emerald-200',
        cancelled && 'bg-red-50 text-red-600 ring-red-200',
        !confirmed && !cancelled && 'bg-neutral-100 text-neutral-500 ring-neutral-200',
      )}
    >
      {confirmed ? 'Confirmed' : cancelled ? 'Cancelled' : status}
    </span>
  )
}

const SYNC_STYLE: Record<string, string> = {
  sent:          'bg-emerald-50 text-emerald-700 ring-emerald-200',
  pending:       'bg-amber-50  text-amber-700  ring-amber-200',
  compensating:  'bg-amber-50  text-amber-700  ring-amber-200',
  failed:        'bg-red-50    text-red-600    ring-red-200',
}

const SYNC_LABEL: Record<string, string> = {
  sent:         'Invite sent',
  pending:      'Invite pending',
  compensating: 'Updating invite',
  failed:       'Invite failed',
}

function SyncBadge({ syncStatus }: { syncStatus: string }) {
  const style = SYNC_STYLE[syncStatus] ?? 'bg-neutral-100 text-neutral-500 ring-neutral-200'
  const label = SYNC_LABEL[syncStatus] ?? syncStatus
  return (
    <span
      title="Calendar invite delivery"
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset cursor-help',
        style,
      )}
    >
      {label}
    </span>
  )
}

// ── Cancel dialog (inline modal) ──────────────────────────────────────────────

interface CancelDialogProps {
  booking: BookingOut
  onConfirm: (series: boolean) => void
  onClose: () => void
  isPending: boolean
}

function CancelDialog({ booking, onConfirm, onClose, isPending }: CancelDialogProps) {
  const isSeries = !!booking.series_id
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm rounded-xl border border-neutral-200 bg-white p-6 shadow-xl space-y-4">
        <h2 className="text-base font-semibold text-neutral-900">Cancel booking</h2>
        <p className="text-sm text-neutral-600">
          {isSeries
            ? `"${booking.title}" is part of a recurring series. Cancel only this occurrence, or the whole series? Cancelling the series keeps past and in-progress occurrences.`
            : `Are you sure you want to cancel "${booking.title}"? This cannot be undone.`}
        </p>
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={isPending}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
          >
            Keep
          </button>
          {isSeries && (
            <button
              type="button"
              onClick={() => onConfirm(false)}
              disabled={isPending}
              className="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
            >
              This occurrence
            </button>
          )}
          <button
            type="button"
            onClick={() => onConfirm(isSeries)}
            disabled={isPending}
            className="inline-flex items-center gap-1.5 rounded-md bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
          >
            {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {isSeries ? 'Cancel series' : 'Cancel booking'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Booking row ───────────────────────────────────────────────────────────────

interface RowProps {
  booking: BookingOut
  showActions: boolean
  onEdit: (b: BookingOut) => void
  onCancel: (b: BookingOut) => void
}

function BookingRow({ booking, showActions, onEdit, onCancel }: RowProps) {
  const isSeries = !!booking.series_id

  return (
    <tr className="border-b border-neutral-100 last:border-0 hover:bg-neutral-50 transition-colors">
      {/* Title + series icon */}
      <td className="px-4 py-3 text-sm">
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-neutral-900">{booking.title}</span>
          {isSeries && (
            <span title={booking.rrule ?? 'Recurring meeting'}>
              <Repeat className="h-3.5 w-3.5 text-neutral-400 shrink-0" />
            </span>
          )}
        </div>
      </td>
      {/* Room */}
      <td className="px-4 py-3 text-sm text-neutral-600">
        <span className="font-medium">{booking.room_name}</span>
        <span className="ml-1 text-xs text-neutral-400">({booking.room_code})</span>
      </td>
      {/* Date + time */}
      <td className="px-4 py-3 text-sm text-neutral-600 whitespace-nowrap">
        <div className="flex items-center gap-1">
          <CalendarDays className="h-3.5 w-3.5 text-neutral-400 shrink-0" />
          <span>{fmtDate(booking.starts_at)}</span>
        </div>
        <div className="text-xs text-neutral-400 mt-0.5 ml-5">
          {fmtTime(booking.starts_at)}–{fmtTime(booking.ends_at)}
        </div>
      </td>
      {/* Attendees */}
      <td className="px-4 py-3 text-sm text-neutral-600">
        <div className="flex items-center gap-1">
          <Users className="h-3.5 w-3.5 text-neutral-400" />
          <span>{booking.attendee_ids.length + 1}</span>
        </div>
      </td>
      {/* Status + sync */}
      <td className="px-4 py-3">
        <div className="flex flex-col gap-1">
          <StatusBadge status={booking.status} />
          <SyncBadge syncStatus={booking.sync_status} />
        </div>
      </td>
      {/* Actions */}
      <td className="px-4 py-3">
        {showActions && (
          <div className="flex items-center gap-1.5">
            {/* Edit — enabled for all bookings including series members (series mode handled in edit page) */}
            <button
              type="button"
              onClick={() => onEdit(booking)}
              title={isSeries ? 'Edit entire series' : 'Edit booking'}
              className="inline-flex items-center gap-1 rounded-md border border-[#085E5E]/30 bg-white px-2.5 py-1 text-xs font-medium text-[#085E5E] hover:bg-[#085E5E]/5 transition-colors"
            >
              <Pencil className="h-3 w-3" />
              Edit
            </button>
            {/* Cancel */}
            <button
              type="button"
              onClick={() => onCancel(booking)}
              className="inline-flex items-center gap-1 rounded-md border border-red-200 bg-white px-2.5 py-1 text-xs font-medium text-red-600 hover:bg-red-50 transition-colors"
            >
              <XIcon className="h-3 w-3" />
              Cancel
            </button>
          </div>
        )}
      </td>
    </tr>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ message }: { message: string }) {
  return (
    <tr>
      <td colSpan={6} className="px-4 py-12 text-center text-sm text-neutral-400">
        {message}
      </td>
    </tr>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

type Tab = 'upcoming' | 'past'

export default function MyBookingsPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [activeTab, setActiveTab] = useState<Tab>('upcoming')
  const [cancelTarget, setCancelTarget] = useState<BookingOut | null>(null)
  const [cancelError, setCancelError] = useState<string | null>(null)

  // Read success note from navigation state (BookingEditPage navigates here with successNote).
  // We seed state from location.state once, then clear the history entry so a refresh
  // doesn't re-show the banner.
  const navState = (location.state as { successNote?: string } | null) ?? {}
  const [successNote, setSuccessNote] = useState<string | null>(navState.successNote ?? null)
  const clearedNavState = useRef(false)
  useEffect(() => {
    if (navState.successNote && !clearedNavState.current) {
      clearedNavState.current = true
      navigate(location.pathname, { replace: true, state: {} })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const { data: bookings, isLoading, error } = useMyBookings()
  const cancel = useCancelBooking()

  // Tick counter so `now` re-evaluates every 60 s even without a data refresh
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 60_000)
    return () => clearInterval(id)
  }, [])

  // Re-compute `now` whenever bookings refresh OR the 60 s tick fires
  const now = useMemo(
    () => new Date().toISOString(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [bookings, tick],
  )

  const upcoming = (bookings ?? []).filter(
    (b) => b.ends_at >= now && b.status === 'confirmed',
  )

  const past = (bookings ?? []).filter(
    (b) => b.ends_at < now || b.status !== 'confirmed',
  )

  const displayed = activeTab === 'upcoming' ? upcoming : past

  function handleEdit(b: BookingOut) {
    navigate(`/my/${b.id}/edit`, { state: { booking: b } })
  }

  function handleCancelConfirm(series: boolean) {
    if (!cancelTarget) return
    cancel.mutate(
      { id: cancelTarget.id, series },
      {
        onSuccess: (data) => {
          setCancelTarget(null)
          setCancelError(null)
          setSuccessNote(`${data.cancelled} booking${data.cancelled !== 1 ? 's' : ''} cancelled.`)
        },
        onError: (err) => {
          setCancelTarget(null)
          setCancelError(err.message)
        },
      },
    )
  }

  const tabCls = (t: Tab) =>
    cn(
      'px-4 py-2 text-sm font-medium border-b-2 transition-colors',
      activeTab === t
        ? 'border-[#085E5E] text-[#085E5E]'
        : 'border-transparent text-neutral-500 hover:text-neutral-700 hover:border-neutral-300',
    )

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 space-y-4">
        {/* Header */}
        <h1 className="text-2xl font-bold text-neutral-900">My Bookings</h1>

        {/* Success note */}
        {successNote && (
          <div className="flex items-center justify-between rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
            <span>{successNote}</span>
            <button
              type="button"
              onClick={() => setSuccessNote(null)}
              className="ml-3 text-emerald-500 hover:text-emerald-700"
            >
              <XIcon className="h-4 w-4" />
            </button>
          </div>
        )}

        {/* Cancel error */}
        {cancelError && (
          <div className="flex items-center justify-between rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{cancelError}</span>
            </div>
            <button
              type="button"
              onClick={() => setCancelError(null)}
              className="ml-3 text-red-400 hover:text-red-600"
            >
              <XIcon className="h-4 w-4" />
            </button>
          </div>
        )}

        {/* Tabs */}
        <div className="flex border-b border-neutral-200">
          <button type="button" className={tabCls('upcoming')} onClick={() => setActiveTab('upcoming')}>
            Upcoming
            {upcoming.length > 0 && (
              <span className="ml-1.5 inline-flex items-center justify-center rounded-full bg-[#085E5E]/10 px-1.5 py-0.5 text-xs font-semibold text-[#085E5E]">
                {upcoming.length}
              </span>
            )}
          </button>
          <button type="button" className={tabCls('past')} onClick={() => setActiveTab('past')}>
            Past
          </button>
        </div>

        {/* Content */}
        {isLoading && (
          <div className="flex items-center justify-center py-16 text-sm text-neutral-500">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            Loading bookings…
          </div>
        )}

        {!isLoading && error && (
          <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Failed to load bookings. Please refresh.
          </div>
        )}

        {!isLoading && !error && (
          <div className="overflow-x-auto rounded-xl border border-neutral-200 bg-white shadow-sm">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-neutral-200 bg-neutral-50">
                <tr>
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">Title</th>
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">Room</th>
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">Date / Time</th>
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">Attendees</th>
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">Status</th>
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">Actions</th>
                </tr>
              </thead>
              <tbody>
                {displayed.length === 0 ? (
                  <EmptyState
                    message={
                      activeTab === 'upcoming'
                        ? 'No upcoming bookings. Book a room to get started.'
                        : 'No past bookings.'
                    }
                  />
                ) : (
                  displayed.map((b) => (
                    <BookingRow
                      key={b.id}
                      booking={b}
                      showActions={activeTab === 'upcoming' && b.status === 'confirmed'}
                      onEdit={handleEdit}
                      onCancel={setCancelTarget}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Cancel dialog */}
      {cancelTarget && (
        <CancelDialog
          booking={cancelTarget}
          onConfirm={handleCancelConfirm}
          onClose={() => setCancelTarget(null)}
          isPending={cancel.isPending}
        />
      )}
    </div>
  )
}
