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
import { useState, useEffect, useRef } from 'react'
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
import { Pagination, SortTh, useSort, sortParams, type Sort } from '@uniops/shell'
import { cn, formatDate, formatTime } from '@/lib/utils'
import { useMyBookingsPaged, useCancelBooking } from '@/services/api'
import type { BookingOut } from '@/lib/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(iso: string): string {
  return formatTime(iso)
}

// ── Status badges ─────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const confirmed = status === 'confirmed'
  const cancelled = status === 'cancelled'
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        confirmed && 'bg-success-soft text-success-fg ring-success-line',
        cancelled && 'bg-danger-soft text-danger-fg ring-danger-line',
        !confirmed && !cancelled && 'bg-surface-sunken text-fg-muted ring-line',
      )}
    >
      {confirmed ? 'Confirmed' : cancelled ? 'Cancelled' : status}
    </span>
  )
}

const SYNC_STYLE: Record<string, string> = {
  sent:          'bg-success-soft text-success-fg ring-success-line',
  pending:       'bg-warning-soft  text-warning-fg  ring-warning-line',
  compensating:  'bg-warning-soft  text-warning-fg  ring-warning-line',
  failed:        'bg-danger-soft    text-danger-fg    ring-danger-line',
}

const SYNC_LABEL: Record<string, string> = {
  sent:         'Invite sent',
  pending:      'Invite pending',
  compensating: 'Updating invite',
  failed:       'Invite failed',
}

function SyncBadge({ syncStatus }: { syncStatus: string }) {
  const style = SYNC_STYLE[syncStatus] ?? 'bg-surface-sunken text-fg-muted ring-line'
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
      <div className="w-full max-w-sm rounded-xl border border-line bg-surface p-6 shadow-xl space-y-4">
        <h2 className="text-base font-semibold text-fg">Cancel booking</h2>
        <p className="text-sm text-fg-secondary">
          {isSeries
            ? `"${booking.title}" is part of a recurring series. Cancel only this occurrence, or the whole series? Cancelling the series keeps past and in-progress occurrences.`
            : `Are you sure you want to cancel "${booking.title}"? This cannot be undone.`}
        </p>
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={isPending}
            className="rounded-md border border-line-strong px-3 py-1.5 text-sm text-fg-secondary hover:bg-surface-subtle disabled:opacity-50"
          >
            Keep
          </button>
          {isSeries && (
            <button
              type="button"
              onClick={() => onConfirm(false)}
              disabled={isPending}
              className="rounded-md border border-danger-line px-3 py-1.5 text-sm font-medium text-danger-fg hover:bg-danger-soft disabled:opacity-50"
            >
              This occurrence
            </button>
          )}
          <button
            type="button"
            onClick={() => onConfirm(isSeries)}
            disabled={isPending}
            className="inline-flex items-center gap-1.5 rounded-md bg-danger-solid px-3 py-1.5 text-sm font-semibold text-white hover:bg-danger-700 disabled:opacity-50"
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
    <tr className="border-b border-line-subtle last:border-0 hover:bg-surface-subtle transition-colors">
      {/* Title + series icon */}
      <td className="px-4 py-3 text-sm">
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-fg">{booking.title}</span>
          {isSeries && (
            <span title={booking.rrule ?? 'Recurring meeting'}>
              <Repeat className="h-3.5 w-3.5 text-fg-muted shrink-0" />
            </span>
          )}
        </div>
      </td>
      {/* Room */}
      <td className="px-4 py-3 text-sm text-fg-secondary">
        <span className="font-medium">{booking.room_name}</span>
        <span className="ml-1 text-xs text-fg-muted">({booking.room_code})</span>
      </td>
      {/* Date + time */}
      <td className="px-4 py-3 text-sm text-fg-secondary whitespace-nowrap">
        <div className="flex items-center gap-1">
          <CalendarDays className="h-3.5 w-3.5 text-fg-muted shrink-0" />
          <span>{formatDate(booking.starts_at)}</span>
        </div>
        <div className="text-xs text-fg-muted mt-0.5 ml-5">
          {fmtTime(booking.starts_at)}–{fmtTime(booking.ends_at)}
        </div>
      </td>
      {/* Attendees */}
      <td className="px-4 py-3 text-sm text-fg-secondary">
        <div className="flex items-center gap-1">
          <Users className="h-3.5 w-3.5 text-fg-muted" />
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
              className="inline-flex items-center gap-1 rounded-md border border-primary-700/30 bg-surface px-2.5 py-1 text-xs font-medium text-primary-700 hover:bg-primary-700/5 transition-colors"
            >
              <Pencil className="h-3 w-3" />
              Edit
            </button>
            {/* Cancel */}
            <button
              type="button"
              onClick={() => onCancel(booking)}
              className="inline-flex items-center gap-1 rounded-md border border-danger-line bg-surface px-2.5 py-1 text-xs font-medium text-danger-fg hover:bg-danger-soft transition-colors"
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
      <td colSpan={6} className="px-4 py-12 text-center text-sm text-fg-muted">
        {message}
      </td>
    </tr>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

type Tab = 'upcoming' | 'past'

/** /bookings/mine/paged ?sort= keys (booking-api MY_BOOKING_SORTABLE). */
type MySortKey = 'title' | 'room' | 'starts_at' | 'attendees' | 'status'

/** Each tab opens on its natural order, shown as the active arrow: Upcoming
 *  soonest first, Past most recent first. */
function tabDefault(tab: Tab): Sort<MySortKey> {
  return { key: 'starts_at', dir: tab === 'upcoming' ? 'asc' : 'desc' }
}

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

  // Server-paged and server-sorted, one tab at a time (/bookings/mine/paged).
  // The split is the same as before — Upcoming = confirmed and not yet ended,
  // Past = everything else — but done by the API, so a long recurring series
  // can no longer push near-term or past bookings out of a 200-row window.
  // The API's 60-second refetch keeps "not yet ended" current.
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [sort, onSort, setSort] = useSort<MySortKey>(tabDefault(activeTab), () => setPage(1))
  useEffect(() => { setPage(1); setSort(tabDefault(activeTab)) }, [activeTab, setSort])
  const { data, isLoading, error } = useMyBookingsPaged({
    scope: activeTab, limit: pageSize, offset: (page - 1) * pageSize, ...sortParams(sort),
  })
  // Upcoming count for the tab badge (the total, not just this page).
  const { data: upcomingCount } = useMyBookingsPaged({ scope: 'upcoming', limit: 1 })
  const cancel = useCancelBooking()

  const displayed = data?.items ?? []
  const total = data?.total ?? 0
  const upcomingTotal = upcomingCount?.total ?? 0

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
        ? 'border-primary-700 text-primary-700'
        : 'border-transparent text-fg-muted hover:text-fg-secondary hover:border-line-strong',
    )

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 space-y-4">
        {/* Header */}
        <h1 className="text-2xl font-bold text-fg">My Bookings</h1>

        {/* Success note */}
        {successNote && (
          <div className="flex items-center justify-between rounded-md border border-success-line bg-success-soft px-4 py-3 text-sm text-success-fg">
            <span>{successNote}</span>
            <button
              type="button"
              onClick={() => setSuccessNote(null)}
              className="ml-3 text-success-fg hover:text-success-fg"
            >
              <XIcon className="h-4 w-4" />
            </button>
          </div>
        )}

        {/* Cancel error */}
        {cancelError && (
          <div className="flex items-center justify-between rounded-md border border-danger-line bg-danger-soft px-4 py-3 text-sm text-danger-fg">
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{cancelError}</span>
            </div>
            <button
              type="button"
              onClick={() => setCancelError(null)}
              className="ml-3 text-danger-400 hover:text-danger-fg"
            >
              <XIcon className="h-4 w-4" />
            </button>
          </div>
        )}

        {/* Tabs */}
        <div className="flex border-b border-line">
          <button type="button" className={tabCls('upcoming')} onClick={() => setActiveTab('upcoming')}>
            Upcoming
            {upcomingTotal > 0 && (
              <span className="ml-1.5 inline-flex items-center justify-center rounded-full bg-primary-700/10 px-1.5 py-0.5 text-xs font-semibold text-primary-700">
                {upcomingTotal}
              </span>
            )}
          </button>
          <button type="button" className={tabCls('past')} onClick={() => setActiveTab('past')}>
            Past
          </button>
        </div>

        {/* Content */}
        {isLoading && (
          <div className="flex items-center justify-center py-16 text-sm text-fg-muted">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            Loading bookings…
          </div>
        )}

        {!isLoading && error && (
          <div className="flex items-center gap-2 rounded-md border border-danger-line bg-danger-soft px-4 py-3 text-sm text-danger-fg">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Failed to load bookings. Please refresh.
          </div>
        )}

        {!isLoading && !error && (
          <div className="overflow-x-auto rounded-xl border border-line bg-surface shadow-sm">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-line bg-surface-subtle">
                <tr>
                  <SortTh label="Title" sortKey="title" sort={sort} onSort={onSort} className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-fg-muted" />
                  <SortTh label="Room" sortKey="room" sort={sort} onSort={onSort} className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-fg-muted" />
                  <SortTh label="Date / Time" sortKey="starts_at" sort={sort} onSort={onSort} className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-fg-muted" />
                  <SortTh label="Attendees" sortKey="attendees" sort={sort} onSort={onSort} className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-fg-muted" />
                  <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-fg-muted" />
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-fg-muted">Actions</th>
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
            {total > 0 && (
              <Pagination
                page={page} pageSize={pageSize} total={total}
                onPageChange={setPage}
                onPageSizeChange={(s) => { setPageSize(s); setPage(1) }}
              />
            )}
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
