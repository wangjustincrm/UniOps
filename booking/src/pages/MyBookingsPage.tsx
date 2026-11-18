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
import { Button, Modal, Pagination, SortTh, TBody, THead, Table, Tabs, Td, Th, Tr, sortParams, type Sort, useSort, Badge, type BadgeTone, Card } from '@uniops/shell'
import { formatDate, formatTime } from '@/lib/utils'
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
    <Badge variant={confirmed ? 'accent' : cancelled ? 'outline' : 'neutral'}>
      {confirmed ? 'Confirmed' : cancelled ? 'Cancelled' : status}
    </Badge>
  )
}

const SYNC_TONE: Record<string, BadgeTone> = {
  sent:          'success',
  pending:       'warning',
  compensating:  'warning',
  failed:        'danger',
}

const SYNC_LABEL: Record<string, string> = {
  sent:         'Invite sent',
  pending:      'Invite pending',
  compensating: 'Updating invite',
  failed:       'Invite failed',
}

function SyncBadge({ syncStatus }: { syncStatus: string }) {
  const tone = SYNC_TONE[syncStatus] ?? 'neutral'
  const label = SYNC_LABEL[syncStatus] ?? syncStatus
  return (
    <Badge variant={tone} title="Calendar invite delivery" className="cursor-help">
      {label}
    </Badge>
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
    <Modal
      open
      onClose={onClose}
      busy={isPending}
      title="Cancel booking"
      // Closes by Keep / the close button only, as before.
      closeOnEscape={false}
      size="full"
      className="max-w-sm"
    >
      <div className="space-y-4">
        <p className="text-sm text-fg-secondary">
          {isSeries
            ? `"${booking.title}" is part of a recurring series. Cancel only this occurrence, or the whole series? Cancelling the series keeps past and in-progress occurrences.`
            : `Are you sure you want to cancel "${booking.title}"? This cannot be undone.`}
        </p>
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="secondary" size="sm"
            type="button"
            onClick={onClose}
            disabled={isPending}
           
          >
            Keep
          </Button>
          {isSeries && (
            <Button variant="danger-outline" size="sm"
              type="button"
              onClick={() => onConfirm(false)}
              disabled={isPending}
             
            >
              This occurrence
            </Button>
          )}
          <Button variant="danger" size="sm"
            type="button"
            onClick={() => onConfirm(isSeries)}
            disabled={isPending}
           
          >
            {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {isSeries ? 'Cancel series' : 'Cancel booking'}
          </Button>
        </div>
      </div>
    </Modal>
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
    <Tr className="hover:bg-surface-subtle transition-colors">
      {/* Title + series icon */}
      <Td className="text-sm">
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-fg">{booking.title}</span>
          {isSeries && (
            <span title={booking.rrule ?? 'Recurring meeting'}>
              <Repeat className="h-3.5 w-3.5 text-fg-muted shrink-0" />
            </span>
          )}
        </div>
      </Td>
      {/* Room */}
      <Td className="text-sm text-fg-secondary">
        <span className="font-medium">{booking.room_name}</span>
        <span className="ml-1 text-xs text-fg-muted">({booking.room_code})</span>
      </Td>
      {/* Date + time */}
      <Td className="text-sm text-fg-secondary whitespace-nowrap">
        <div className="flex items-center gap-1">
          <CalendarDays className="h-3.5 w-3.5 text-fg-muted shrink-0" />
          <span>{formatDate(booking.starts_at)}</span>
        </div>
        <div className="text-xs text-fg-muted mt-0.5 ml-5">
          {fmtTime(booking.starts_at)}–{fmtTime(booking.ends_at)}
        </div>
      </Td>
      {/* Attendees */}
      <Td className="text-sm text-fg-secondary">
        <div className="flex items-center gap-1">
          <Users className="h-3.5 w-3.5 text-fg-muted" />
          <span>{booking.attendee_ids.length + 1}</span>
        </div>
      </Td>
      {/* Status + sync */}
      <Td>
        <div className="flex flex-col gap-1">
          <StatusBadge status={booking.status} />
          <SyncBadge syncStatus={booking.sync_status} />
        </div>
      </Td>
      {/* Actions */}
      <Td>
        {showActions && (
          <div className="flex items-center gap-1.5">
            {/* Edit — enabled for all bookings including series members (series mode handled in edit page) */}
            <Button variant="outline" size="xs"
              type="button"
              onClick={() => onEdit(booking)}
              title={isSeries ? 'Edit entire series' : 'Edit booking'}
             
            >
              <Pencil className="h-3 w-3" />
              Edit
            </Button>
            {/* Cancel */}
            <Button variant="danger-outline" size="xs"
              type="button"
              onClick={() => onCancel(booking)}
             
            >
              <XIcon className="h-3 w-3" />
              Cancel
            </Button>
          </div>
        )}
      </Td>
    </Tr>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ message }: { message: string }) {
  return (
    <Tr>
      <Td align="center" colSpan={6} className="text-sm text-fg-muted">
        {message}
      </Td>
    </Tr>
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


  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 space-y-4">
        {/* Header */}
        <h1 className="text-2xl font-semibold text-fg">My Bookings</h1>

        {/* Success note */}
        {successNote && (
          <div className="flex items-center justify-between rounded-md border border-success-line bg-success-soft px-4 py-3 text-sm text-success-fg">
            <span>{successNote}</span>
            <Button aria-label="Dismiss" variant="subtle" size="icon-xs"
              type="button"
              onClick={() => setSuccessNote(null)}
              className="ml-3"
            >
              <XIcon className="h-4 w-4" />
            </Button>
          </div>
        )}

        {/* Cancel error */}
        {cancelError && (
          <div className="flex items-center justify-between rounded-md border border-danger-line bg-danger-soft px-4 py-3 text-sm text-danger-fg">
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{cancelError}</span>
            </div>
            <Button aria-label="Dismiss" variant="subtle" size="icon-xs"
              type="button"
              onClick={() => setCancelError(null)}
              className="ml-3 text-danger-fg hover:bg-danger-soft"
            >
              <XIcon className="h-4 w-4" />
            </Button>
          </div>
        )}

        {/* Tabs */}
        <Tabs<Tab>
          items={[{ key: 'upcoming', label: 'Upcoming', count: upcomingTotal > 0 ? upcomingTotal : undefined }, { key: 'past', label: 'Past' }]}
          value={activeTab}
          onChange={setActiveTab}
        />

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
          <Card className="overflow-x-auto">
            <Table className="text-left">
              <THead>
                <Tr>
                  <SortTh label="Title" sortKey="title" sort={sort} onSort={onSort} className="px-4 py-3 text-xs font-semibold text-fg-muted" />
                  <SortTh label="Room" sortKey="room" sort={sort} onSort={onSort} className="px-4 py-3 text-xs font-semibold text-fg-muted" />
                  <SortTh label="Date / Time" sortKey="starts_at" sort={sort} onSort={onSort} className="px-4 py-3 text-xs font-semibold text-fg-muted" />
                  <SortTh label="Attendees" sortKey="attendees" sort={sort} onSort={onSort} className="px-4 py-3 text-xs font-semibold text-fg-muted" />
                  <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-4 py-3 text-xs font-semibold text-fg-muted" />
                  <Th>Actions</Th>
                </Tr>
              </THead>
              <TBody>
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
              </TBody>
            </Table>
            {total > 0 && (
              <Pagination
                page={page} pageSize={pageSize} total={total}
                onPageChange={setPage}
                onPageSizeChange={(s) => { setPageSize(s); setPage(1) }}
              />
            )}
          </Card>
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
