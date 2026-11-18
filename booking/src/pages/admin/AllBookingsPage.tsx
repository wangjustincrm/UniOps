/**
 * AllBookingsPage — /admin/bookings
 *
 * Filters (server-side): room_id, date_from, date_to, status, organizer (name contains)
 * Pagination: limit=50, offset-based with total from response
 *
 * Table: title, room, organizer_name, time range, status, sync_status
 * Actions: Force Cancel (confirm dialog, series-aware), Export CSV
 */
import { useState, useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import {
  Loader2,
  AlertCircle,
  Download,
  X as XIcon,
  Repeat,
} from 'lucide-react'
import { SortTh, useSort, sortParams, Input, Select, TBody, THead, Table, Td, Th, Tr, Button, Modal, Badge, type BadgeTone } from '@uniops/shell'
import { cn, formatDate, formatTime } from '@/lib/utils'
import {
  useAdminRoomList,
  useAdminBookings,
  useAdminForceCancel,
  adminBookingService,
} from '@/services/api'
import type { BookingAdminOut, AdminBookingFilters } from '@/lib/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(iso: string): string {
  return formatTime(iso)
}

const STATUS_TONE: Record<string, BadgeTone> = {
  confirmed:  'accent',
  cancelled:  'outline',
  completed:  'success',
  pending:    'warning',
  no_show:    'danger',
}

const SYNC_TONE: Record<string, BadgeTone> = {
  sent:          'success',
  pending:       'warning',
  compensating:  'warning',
  failed:        'danger',
}

const SYNC_LABEL: Record<string, string> = {
  sent: 'Sent', pending: 'Pending', compensating: 'Updating', failed: 'Failed',
}

function BookingStatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONE[status] ?? 'neutral'
  const label = status.charAt(0).toUpperCase() + status.slice(1).replace('_', ' ')
  return (
    <Badge variant={tone}>
      {label}
    </Badge>
  )
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

// ── Force Cancel dialog ───────────────────────────────────────────────────────

interface ForceCancelDialogProps {
  booking: BookingAdminOut
  onConfirm: (series: boolean) => void
  onClose: () => void
  isPending: boolean
}

function ForceCancelDialog({ booking, onConfirm, onClose, isPending }: ForceCancelDialogProps) {
  const isSeries = !!booking.series_id
  return (
    <Modal
      open
      onClose={onClose}
      busy={isPending}
      title="Force Cancel Booking"
      // Closes by Keep / the close button only, as before.
      closeOnEscape={false}
      size="full"
      className="max-w-sm"
    >
      <div className="space-y-4">
        <p className="text-sm text-fg-secondary">
          {isSeries
            ? `"${booking.title}" booked by ${booking.organizer_name} is part of a recurring series. Cancel only this occurrence, or the whole series? Cancelling the series keeps past and in-progress occurrences.`
            : `Cancel "${booking.title}" booked by ${booking.organizer_name}? This cannot be undone.`}
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
            {isSeries ? 'Cancel series' : 'Force cancel'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

const PAGE_SIZE = 50

export default function AllBookingsPage() {
  const location = useLocation()
  const searchParams = new URLSearchParams(location.search)
  const presetRoomId = searchParams.get('room_id') ?? ''

  const [roomId, setRoomId] = useState(presetRoomId)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [status, setStatus] = useState('')
  const [organizerFilter, setOrganizerFilter] = useState('')
  const [offset, setOffset] = useState(0)
  // Server-sorted: the list is paged by limit/offset (no sort = newest start first).
  const [sort, onSort] = useSort<'title' | 'room' | 'organizer' | 'starts_at' | 'status' | 'sync_status'>(
    null, () => setOffset(0))

  const [cancelTarget, setCancelTarget] = useState<BookingAdminOut | null>(null)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const [successNote, setSuccessNote] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  // Organizer name is filtered by the API (it used to match only the 50 rows on
  // screen); debounced so typing doesn't fire a request per keystroke.
  const [organizerQuery, setOrganizerQuery] = useState('')
  useEffect(() => {
    const id = setTimeout(() => setOrganizerQuery(organizerFilter.trim()), 300)
    return () => clearTimeout(id)
  }, [organizerFilter])

  // Reset offset on filter change
  useEffect(() => { setOffset(0) }, [roomId, dateFrom, dateTo, status, organizerQuery])

  const serverFilters: AdminBookingFilters = {
    room_id: roomId || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    status: status || undefined,
    organizer: organizerQuery || undefined,
    limit: PAGE_SIZE,
    offset,
    ...sortParams(sort),
  }

  const { data, isLoading, error, refetch } = useAdminBookings(serverFilters)
  const { data: rooms } = useAdminRoomList()
  const forceCancel = useAdminForceCancel()

  const items = data?.items ?? []

  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / PAGE_SIZE)
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  function handleCancelConfirm(series: boolean) {
    if (!cancelTarget) return
    forceCancel.mutate(
      { id: cancelTarget.id, series },
      {
        onSuccess: (res) => {
          setCancelTarget(null)
          setCancelError(null)
          setSuccessNote(`${res.cancelled} booking${res.cancelled !== 1 ? 's' : ''} cancelled.`)
          void refetch()
        },
        onError: (err) => {
          setCancelTarget(null)
          setCancelError(err.message)
        },
      },
    )
  }

  async function handleExport() {
    setExporting(true)
    setExportError(null)
    try {
      await adminBookingService.exportCsv({
        room_id: roomId || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        status: status || undefined,
        organizer: organizerQuery || undefined,
      })
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  const thCls = 'px-4 py-3 text-xs font-semibold text-fg-muted text-left'
  const tdCls = 'px-4 py-3 text-sm'

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-fg">All Bookings</h1>
          <Button variant="secondary" size="sm"
            type="button"
            onClick={handleExport}
            disabled={exporting}
           
          >
            {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            Export CSV
          </Button>
        </div>

        {/* Banners */}
        {successNote && (
          <div className="flex items-center justify-between rounded-md border border-success-line bg-success-soft px-4 py-2.5 text-sm text-success-fg">
            <span>{successNote}</span>
            <Button variant="subtle" size="icon-xs" aria-label="Clear" type="button" onClick={() => setSuccessNote(null)}><XIcon className="h-4 w-4 text-success-fg" /></Button>
          </div>
        )}
        {cancelError && (
          <div className="flex items-center gap-2 rounded-md border border-danger-line bg-danger-soft px-4 py-2.5 text-sm text-danger-fg">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {cancelError}
            <Button aria-label="Dismiss" variant="subtle" size="icon-xs" type="button" onClick={() => setCancelError(null)} className="ml-auto"><XIcon className="h-4 w-4" /></Button>
          </div>
        )}
        {exportError && (
          <div className="flex items-center gap-2 rounded-md border border-danger-line bg-danger-soft px-4 py-2.5 text-sm text-danger-fg">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Export failed: {exportError}
            <Button aria-label="Dismiss" variant="subtle" size="icon-xs" type="button" onClick={() => setExportError(null)} className="ml-auto"><XIcon className="h-4 w-4" /></Button>
          </div>
        )}

        {/* Filters */}
        <div className="flex flex-wrap gap-3">
          <Select
            value={roomId}
            onChange={(e) => setRoomId(e.target.value)}
           
          >
            <option value="">All Rooms</option>
            {(rooms ?? []).map((r) => (
              <option key={r.id} value={r.id}>{r.name} ({r.code})</option>
            ))}
          </Select>
          <div className="flex items-center gap-1.5">
            <label className="text-xs text-fg-muted">From</label>
            <Input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
             
            />
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-xs text-fg-muted">To</label>
            <Input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
             
            />
          </div>
          <Select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
           
          >
            <option value="">All Statuses</option>
            <option value="confirmed">Confirmed</option>
            <option value="cancelled">Cancelled</option>
            <option value="completed">Completed</option>
            <option value="no_show">No Show</option>
          </Select>
          <Input
            value={organizerFilter}
            onChange={(e) => setOrganizerFilter(e.target.value)}
            placeholder="Organizer name…"
            className="w-44"
          />
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
            Failed to load bookings.
          </div>
        )}
        {!isLoading && !error && (
          <>
            <div className="overflow-x-auto rounded-xl border border-line bg-surface shadow-sm">
              <Table className="text-left">
                <THead>
                  <Tr>
                    <SortTh label="Title" sortKey="title" sort={sort} onSort={onSort} className={thCls} />
                    <SortTh label="Room" sortKey="room" sort={sort} onSort={onSort} className={thCls} />
                    <SortTh label="Organizer" sortKey="organizer" sort={sort} onSort={onSort} className={thCls} />
                    <SortTh label="Date / Time" sortKey="starts_at" sort={sort} onSort={onSort} className={thCls} />
                    <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className={thCls} />
                    <SortTh label="Sync" sortKey="sync_status" sort={sort} onSort={onSort} className={thCls} />
                    <Th className={thCls}>Actions</Th>
                  </Tr>
                </THead>
                <TBody>
                  {items.length === 0 ? (
                    <Tr>
                      <Td align="center" colSpan={7} className="text-sm text-fg-muted">
                        No bookings found.
                      </Td>
                    </Tr>
                  ) : (
                    items.map((b) => (
                      <Tr key={b.id} className="hover:bg-surface-subtle transition-colors">
                        <Td className={tdCls}>
                          <div className="flex items-center gap-1.5">
                            <span className="font-medium text-fg">{b.title}</span>
                            {b.series_id && (
                              <span title={b.rrule ?? 'Recurring'}>
                                <Repeat className="h-3.5 w-3.5 text-fg-muted" />
                              </span>
                            )}
                          </div>
                        </Td>
                        <Td className={tdCls}>
                          <span className="font-medium">{b.room_name}</span>
                          <span className="ml-1 text-xs text-fg-muted">({b.room_code})</span>
                        </Td>
                        <Td className={cn(tdCls, 'text-fg-secondary')}>{b.organizer_name}</Td>
                        <Td className={cn(tdCls, 'whitespace-nowrap')}>
                          <div className="text-fg-secondary">{formatDate(b.starts_at)}</div>
                          <div className="text-xs text-fg-muted">{fmtTime(b.starts_at)}–{fmtTime(b.ends_at)}</div>
                        </Td>
                        <Td className={tdCls}>
                          <BookingStatusBadge status={b.status} />
                        </Td>
                        <Td className={tdCls}>
                          <SyncBadge syncStatus={b.sync_status} />
                        </Td>
                        <Td className={tdCls}>
                          {b.status === 'confirmed' && (
                            <Button variant="danger-outline" size="xs"
                              type="button"
                              onClick={() => setCancelTarget(b)}
                             
                            >
                              <XIcon className="h-3 w-3" />
                              Cancel
                            </Button>
                          )}
                        </Td>
                      </Tr>
                    ))
                  )}
                </TBody>
              </Table>
            </div>

            {/* Pagination */}
            {total > PAGE_SIZE && (
              <div className="flex items-center justify-between text-sm text-fg-secondary">
                <span>{total} total</span>
                <div className="flex items-center gap-2">
                  <Button variant="secondary" size="xs"
                    type="button"
                    disabled={offset === 0}
                    onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                   
                  >
                    Previous
                  </Button>
                  <span>Page {currentPage} of {totalPages}</span>
                  <Button variant="secondary" size="xs"
                    type="button"
                    disabled={offset + PAGE_SIZE >= total}
                    onClick={() => setOffset((o) => o + PAGE_SIZE)}
                   
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Cancel dialog */}
      {cancelTarget && (
        <ForceCancelDialog
          booking={cancelTarget}
          onConfirm={handleCancelConfirm}
          onClose={() => setCancelTarget(null)}
          isPending={forceCancel.isPending}
        />
      )}
    </div>
  )
}
