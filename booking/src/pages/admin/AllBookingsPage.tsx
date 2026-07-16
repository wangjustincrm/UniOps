/**
 * AllBookingsPage — /admin/bookings
 *
 * Filters (server-side): room_id, date_from, date_to, status
 * Organizer name filter: client-side contains match on organizer_name
 * Pagination: limit=50, offset-based with total from response
 *
 * Table: title, room, organizer_name, time range, status, sync_status
 * Actions: Force Cancel (confirm dialog, series-aware), Export CSV
 */
import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useLocation } from 'react-router-dom'
import {
  Loader2,
  AlertCircle,
  Download,
  X as XIcon,
  Repeat,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  useAdminRoomList,
  useAdminBookings,
  useAdminForceCancel,
  adminBookingService,
} from '@/services/api'
import type { BookingAdminOut, AdminBookingFilters } from '@/lib/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-CA', { month: 'short', day: 'numeric', year: 'numeric' })
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit', hour12: false })
}

const STATUS_STYLE: Record<string, string> = {
  confirmed:  'bg-emerald-50 text-emerald-700 ring-emerald-200',
  cancelled:  'bg-neutral-100 text-neutral-400 ring-neutral-200',
  completed:  'bg-sky-50 text-sky-700 ring-sky-200',
  pending:    'bg-amber-50 text-amber-700 ring-amber-200',
  no_show:    'bg-red-50 text-red-600 ring-red-200',
}

const SYNC_STYLE: Record<string, string> = {
  sent:          'bg-emerald-50 text-emerald-700 ring-emerald-200',
  pending:       'bg-amber-50  text-amber-700  ring-amber-200',
  compensating:  'bg-amber-50  text-amber-700  ring-amber-200',
  failed:        'bg-red-50    text-red-600    ring-red-200',
}

const SYNC_LABEL: Record<string, string> = {
  sent: 'Sent', pending: 'Pending', compensating: 'Updating', failed: 'Failed',
}

function BookingStatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLE[status] ?? 'bg-neutral-100 text-neutral-500 ring-neutral-200'
  const label = status.charAt(0).toUpperCase() + status.slice(1).replace('_', ' ')
  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset', style)}>
      {label}
    </span>
  )
}

function SyncBadge({ syncStatus }: { syncStatus: string }) {
  const style = SYNC_STYLE[syncStatus] ?? 'bg-neutral-100 text-neutral-500 ring-neutral-200'
  const label = SYNC_LABEL[syncStatus] ?? syncStatus
  return (
    <span title="Calendar invite delivery" className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset cursor-help', style)}>
      {label}
    </span>
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
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm rounded-xl border border-neutral-200 bg-white p-6 shadow-xl space-y-4">
        <h2 className="text-base font-semibold text-neutral-900">Force Cancel Booking</h2>
        <p className="text-sm text-neutral-600">
          {isSeries
            ? `"${booking.title}" booked by ${booking.organizer_name} is part of a recurring series. Cancel only this occurrence, or the whole series? Cancelling the series keeps past and in-progress occurrences.`
            : `Cancel "${booking.title}" booked by ${booking.organizer_name}? This cannot be undone.`}
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
            {isSeries ? 'Cancel series' : 'Force cancel'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
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

  const [cancelTarget, setCancelTarget] = useState<BookingAdminOut | null>(null)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const [successNote, setSuccessNote] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  // Reset offset on filter change
  useEffect(() => { setOffset(0) }, [roomId, dateFrom, dateTo, status])

  const serverFilters: AdminBookingFilters = {
    room_id: roomId || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    status: status || undefined,
    limit: PAGE_SIZE,
    offset,
  }

  const { data, isLoading, error, refetch } = useAdminBookings(serverFilters)
  const { data: rooms } = useAdminRoomList()
  const forceCancel = useAdminForceCancel()

  // Client-side organizer filter
  const items = (data?.items ?? []).filter((b) =>
    organizerFilter
      ? b.organizer_name.toLowerCase().includes(organizerFilter.toLowerCase())
      : true,
  )

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
      })
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  const thCls = 'px-4 py-3 text-xs font-semibold uppercase tracking-wider text-neutral-500 text-left'
  const tdCls = 'px-4 py-3 text-sm'

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-neutral-900">All Bookings</h1>
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting}
            className="inline-flex items-center gap-1.5 rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
          >
            {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            Export CSV
          </button>
        </div>

        {/* Banners */}
        {successNote && (
          <div className="flex items-center justify-between rounded-md border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-700">
            <span>{successNote}</span>
            <button type="button" onClick={() => setSuccessNote(null)}><XIcon className="h-4 w-4 text-emerald-500" /></button>
          </div>
        )}
        {cancelError && (
          <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-600">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {cancelError}
            <button type="button" onClick={() => setCancelError(null)} className="ml-auto"><XIcon className="h-4 w-4" /></button>
          </div>
        )}
        {exportError && (
          <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-600">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Export failed: {exportError}
            <button type="button" onClick={() => setExportError(null)} className="ml-auto"><XIcon className="h-4 w-4" /></button>
          </div>
        )}

        {/* Filters */}
        <div className="flex flex-wrap gap-3">
          <select
            value={roomId}
            onChange={(e) => setRoomId(e.target.value)}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
          >
            <option value="">All Rooms</option>
            {(rooms ?? []).map((r) => (
              <option key={r.id} value={r.id}>{r.name} ({r.code})</option>
            ))}
          </select>
          <div className="flex items-center gap-1.5">
            <label className="text-xs text-neutral-500">From</label>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
            />
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-xs text-neutral-500">To</label>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
            />
          </div>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
          >
            <option value="">All Statuses</option>
            <option value="confirmed">Confirmed</option>
            <option value="cancelled">Cancelled</option>
            <option value="completed">Completed</option>
            <option value="no_show">No Show</option>
          </select>
          <input
            value={organizerFilter}
            onChange={(e) => setOrganizerFilter(e.target.value)}
            placeholder="Organizer name…"
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40 w-44"
          />
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
            Failed to load bookings.
          </div>
        )}
        {!isLoading && !error && (
          <>
            <div className="overflow-x-auto rounded-xl border border-neutral-200 bg-white shadow-sm">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-neutral-200 bg-neutral-50">
                  <tr>
                    <th className={thCls}>Title</th>
                    <th className={thCls}>Room</th>
                    <th className={thCls}>Organizer</th>
                    <th className={thCls}>Date / Time</th>
                    <th className={thCls}>Status</th>
                    <th className={thCls}>Sync</th>
                    <th className={thCls}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-12 text-center text-sm text-neutral-400">
                        No bookings found.
                      </td>
                    </tr>
                  ) : (
                    items.map((b) => (
                      <tr key={b.id} className="border-b border-neutral-100 last:border-0 hover:bg-neutral-50 transition-colors">
                        <td className={tdCls}>
                          <div className="flex items-center gap-1.5">
                            <span className="font-medium text-neutral-900">{b.title}</span>
                            {b.series_id && (
                              <span title={b.rrule ?? 'Recurring'}>
                                <Repeat className="h-3.5 w-3.5 text-neutral-400" />
                              </span>
                            )}
                          </div>
                        </td>
                        <td className={tdCls}>
                          <span className="font-medium">{b.room_name}</span>
                          <span className="ml-1 text-xs text-neutral-400">({b.room_code})</span>
                        </td>
                        <td className={cn(tdCls, 'text-neutral-600')}>{b.organizer_name}</td>
                        <td className={cn(tdCls, 'whitespace-nowrap')}>
                          <div className="text-neutral-600">{fmtDate(b.starts_at)}</div>
                          <div className="text-xs text-neutral-400">{fmtTime(b.starts_at)}–{fmtTime(b.ends_at)}</div>
                        </td>
                        <td className={tdCls}>
                          <BookingStatusBadge status={b.status} />
                        </td>
                        <td className={tdCls}>
                          <SyncBadge syncStatus={b.sync_status} />
                        </td>
                        <td className={tdCls}>
                          {b.status === 'confirmed' && (
                            <button
                              type="button"
                              onClick={() => setCancelTarget(b)}
                              className="inline-flex items-center gap-1 rounded-md border border-red-200 bg-white px-2.5 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
                            >
                              <XIcon className="h-3 w-3" />
                              Cancel
                            </button>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {total > PAGE_SIZE && (
              <div className="flex items-center justify-between text-sm text-neutral-600">
                <span>{total} total</span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    disabled={offset === 0}
                    onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                    className="rounded-md border border-neutral-300 px-3 py-1 text-sm disabled:opacity-40 hover:bg-neutral-50"
                  >
                    Previous
                  </button>
                  <span>Page {currentPage} of {totalPages}</span>
                  <button
                    type="button"
                    disabled={offset + PAGE_SIZE >= total}
                    onClick={() => setOffset((o) => o + PAGE_SIZE)}
                    className="rounded-md border border-neutral-300 px-3 py-1 text-sm disabled:opacity-40 hover:bg-neutral-50"
                  >
                    Next
                  </button>
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
