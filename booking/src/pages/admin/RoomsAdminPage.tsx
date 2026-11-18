/**
 * RoomsAdminPage — /admin/rooms
 *
 * Filter bar: floor / area / status (native selects)
 * Table: code, name, location, capacity, type, status, actions (Edit + Status)
 * Header: New Room button + Import button
 *
 * Status-change inline control:
 *   - select new status + optional notes
 *   - if affected_future_bookings > 0 → warning with link to /admin/bookings?room_id=
 *
 * 409 duplicate code → inline error in RoomFormModal.
 */
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import {
  Loader2,
  AlertCircle,
  Plus,
  Upload,
  Pencil,
  ShieldAlert,
  X as XIcon,
  CheckCircle2,
} from 'lucide-react'
import { SortTh, useSortableRows } from '@uniops/shell'
import { cn } from '@/lib/utils'
import {
  useAdminRoomList,
  useAdminSetRoomStatus,
} from '@/services/api'
import type { RoomOut } from '@/lib/types'
import { ROOM_TYPE_LABELS } from '@/lib/types'
import { RoomFormModal } from './RoomFormModal'
import { RoomImportModal } from './RoomImportModal'
import type { RoomType } from '@/lib/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

const STATUS_STYLE: Record<string, string> = {
  available:   'bg-success-soft text-success-fg ring-success-line',
  disabled:    'bg-surface-sunken text-fg-muted ring-line',
  maintenance: 'bg-warning-soft text-warning-fg ring-warning-line',
}

function RoomStatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLE[status] ?? 'bg-surface-sunken text-fg-muted ring-line'
  const label = status.charAt(0).toUpperCase() + status.slice(1)
  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset', style)}>
      {label}
    </span>
  )
}

function locationStr(room: RoomOut): string {
  return [room.floor && `Floor ${room.floor}`, room.area]
    .filter(Boolean)
    .join(' · ')
}

// ── Status change dialog ──────────────────────────────────────────────────────

interface StatusChangeDialogProps {
  room: RoomOut
  onClose: () => void
}

function StatusChangeDialog({ room, onClose }: StatusChangeDialogProps) {
  const navigate = useNavigate()
  const [status, setStatus] = useState<string>(room.status)
  const [notes, setNotes] = useState('')
  const [affectedWarning, setAffectedWarning] = useState<number | null>(null)
  const mut = useAdminSetRoomStatus(room.id)

  async function handleApply() {
    try {
      const res = await mut.mutateAsync({ status, notes: notes || undefined })
      if (res.affected_future_bookings > 0) {
        setAffectedWarning(res.affected_future_bookings)
      } else {
        onClose()
      }
    } catch {
      // error surfaced via mut.error
    }
  }

  const modal = (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm rounded-xl border border-line bg-surface shadow-xl p-6 space-y-4">
        <h2 className="text-base font-semibold text-fg">Change Room Status</h2>
        <p className="text-sm text-fg-secondary">
          Room: <span className="font-medium">{room.name}</span> ({room.code})
        </p>

        {affectedWarning !== null ? (
          <div className="space-y-3">
            <div className="flex items-start gap-2 rounded-md border border-warning-line bg-warning-soft p-3 text-sm text-warning-fg">
              <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
              <span>
                <strong>{affectedWarning}</strong> future booking{affectedWarning !== 1 ? 's' : ''} are affected.
                Review them in{' '}
                <button
                  type="button"
                  onClick={() => navigate(`/admin/bookings?room_id=${room.id}`)}
                  className="underline text-warning-fg hover:text-warning-fg font-medium"
                >
                  All Bookings
                </button>.
              </span>
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md border border-line-strong px-3 py-1.5 text-sm text-fg-secondary hover:bg-surface-subtle"
              >
                Close
              </button>
              <button
                type="button"
                onClick={() => navigate(`/admin/bookings?room_id=${room.id}`)}
                className="rounded-md bg-primary-700 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700/90"
              >
                View Bookings
              </button>
            </div>
          </div>
        ) : (
          <>
            <div>
              <label className="block text-xs font-medium text-fg-secondary mb-1">New Status</label>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="w-full rounded-md border border-line-strong px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary-700/40"
              >
                <option value="available">Available</option>
                <option value="disabled">Disabled</option>
                <option value="maintenance">Maintenance</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-fg-secondary mb-1">
                Notes {status === 'maintenance' && <span className="text-danger-fg">*</span>}
              </label>
              <textarea
                rows={2}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                required={status === 'maintenance'}
                className="w-full rounded-md border border-line-strong px-3 py-1.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-primary-700/40"
                placeholder={status === 'maintenance' ? 'Describe the maintenance work' : 'Optional reason'}
              />
            </div>

            {mut.error && (
              <p className="text-xs text-danger-fg flex items-center gap-1">
                <AlertCircle className="h-3.5 w-3.5" />
                {mut.error.message}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                disabled={mut.isPending}
                className="rounded-md border border-line-strong px-3 py-1.5 text-sm text-fg-secondary hover:bg-surface-subtle disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleApply}
                disabled={mut.isPending || (status === 'maintenance' && !notes.trim())}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary-700 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700/90 disabled:opacity-50"
              >
                {mut.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Apply
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )

  return createPortal(modal, document.body)
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RoomsAdminPage() {
  const [filters, setFilters] = useState({ floor: '', area: '', status: '' })
  const [formTarget, setFormTarget] = useState<RoomOut | null | undefined>(undefined)
  // undefined = closed; null = new room; RoomOut = editing
  const [showImport, setShowImport] = useState(false)
  const [statusTarget, setStatusTarget] = useState<RoomOut | null>(null)
  const [successNote, setSuccessNote] = useState<string | null>(null)

  const activeFilters = {
    floor: filters.floor || undefined,
    area: filters.area || undefined,
    status: filters.status || undefined,
  }

  const { data: rooms, isLoading, error, refetch } = useAdminRoomList(activeFilters)
  // Whole list (/admin/rooms is not paged) → sorted here, by what each cell shows.
  const sorted = useSortableRows(rooms, {
    code: (r) => r.code,
    name: (r) => r.name,
    location: (r) => locationStr(r),
    capacity: (r) => r.capacity,
    type: (r) => ROOM_TYPE_LABELS[r.room_type as RoomType] ?? r.room_type,
    status: (r) => r.status,
  })

  function handleSaved(room: RoomOut) {
    setFormTarget(undefined)
    setSuccessNote(`Room "${room.name}" saved.`)
    void refetch()
  }

  const thCls = 'px-4 py-3 text-xs font-semibold uppercase tracking-wider text-fg-muted text-left'
  const tdCls = 'px-4 py-3 text-sm'

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-fg">Rooms</h1>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowImport(true)}
              className="inline-flex items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-fg-secondary hover:bg-surface-subtle"
            >
              <Upload className="h-4 w-4" />
              Import
            </button>
            <button
              type="button"
              onClick={() => setFormTarget(null)}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary-700 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700/90"
            >
              <Plus className="h-4 w-4" />
              New Room
            </button>
          </div>
        </div>

        {/* Success note */}
        {successNote && (
          <div className="flex items-center justify-between rounded-md border border-success-line bg-success-soft px-4 py-2.5 text-sm text-success-fg">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4" />
              {successNote}
            </div>
            <button type="button" onClick={() => setSuccessNote(null)}>
              <XIcon className="h-4 w-4 text-success-fg" />
            </button>
          </div>
        )}

        {/* Filter bar */}
        <div className="flex flex-wrap gap-3">
          <input
            value={filters.floor}
            onChange={(e) => setFilters((f) => ({ ...f, floor: e.target.value }))}
            placeholder="Floor…"
            className="rounded-md border border-line-strong px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary-700/40 w-32"
          />
          <input
            value={filters.area}
            onChange={(e) => setFilters((f) => ({ ...f, area: e.target.value }))}
            placeholder="Area…"
            className="rounded-md border border-line-strong px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary-700/40 w-36"
          />
          <select
            value={filters.status}
            onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
            className="rounded-md border border-line-strong px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary-700/40"
          >
            <option value="">All Statuses</option>
            <option value="available">Available</option>
            <option value="disabled">Disabled</option>
            <option value="maintenance">Maintenance</option>
          </select>
          {(filters.floor || filters.area || filters.status) && (
            <button
              type="button"
              onClick={() => setFilters({ floor: '', area: '', status: '' })}
              className="text-xs text-fg-muted hover:text-fg-secondary underline"
            >
              Clear filters
            </button>
          )}
        </div>

        {/* Content */}
        {isLoading && (
          <div className="flex items-center justify-center py-16 text-sm text-fg-muted">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            Loading rooms…
          </div>
        )}

        {!isLoading && error && (
          <div className="flex items-center gap-2 rounded-md border border-danger-line bg-danger-soft px-4 py-3 text-sm text-danger-fg">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Failed to load rooms. Please refresh.
          </div>
        )}

        {!isLoading && !error && (
          <div className="overflow-x-auto rounded-xl border border-line bg-surface shadow-sm">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-line bg-surface-subtle">
                <tr>
                  <SortTh label="Code" sortKey="code" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <SortTh label="Name" sortKey="name" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <SortTh label="Location" sortKey="location" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <SortTh label="Cap." sortKey="capacity" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <SortTh label="Type" sortKey="type" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <SortTh label="Status" sortKey="status" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <th className={thCls}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {(rooms ?? []).length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-12 text-center text-sm text-fg-muted">
                      No rooms found. Add one or adjust filters.
                    </td>
                  </tr>
                ) : (
                  sorted.rows.map((room) => (
                    <tr
                      key={room.id}
                      className="border-b border-line-subtle last:border-0 hover:bg-surface-subtle transition-colors"
                    >
                      <td className={cn(tdCls, 'font-mono text-xs text-fg-secondary')}>{room.code}</td>
                      <td className={cn(tdCls, 'font-medium text-fg')}>{room.name}</td>
                      <td className={cn(tdCls, 'text-fg-muted max-w-[180px] truncate')} title={locationStr(room)}>
                        {locationStr(room) || '—'}
                      </td>
                      <td className={tdCls}>{room.capacity}</td>
                      <td className={tdCls}>
                        {ROOM_TYPE_LABELS[room.room_type as RoomType] ?? room.room_type}
                      </td>
                      <td className={tdCls}>
                        <RoomStatusBadge status={room.status} />
                      </td>
                      <td className={tdCls}>
                        <div className="flex items-center gap-1.5">
                          <button
                            type="button"
                            onClick={() => setFormTarget(room)}
                            className="inline-flex items-center gap-1 rounded-md border border-primary-700/30 bg-surface px-2.5 py-1 text-xs font-medium text-primary-700 hover:bg-primary-700/5"
                          >
                            <Pencil className="h-3 w-3" />
                            Edit
                          </button>
                          <button
                            type="button"
                            onClick={() => setStatusTarget(room)}
                            className="inline-flex items-center gap-1 rounded-md border border-warning-line bg-surface px-2.5 py-1 text-xs font-medium text-warning-fg hover:bg-warning-soft"
                          >
                            <ShieldAlert className="h-3 w-3" />
                            Status
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modals */}
      {formTarget !== undefined && (
        <RoomFormModal
          room={formTarget}
          onClose={() => setFormTarget(undefined)}
          onSaved={handleSaved}
        />
      )}
      {showImport && (
        <RoomImportModal onClose={() => { setShowImport(false); void refetch() }} />
      )}
      {statusTarget && (
        <StatusChangeDialog
          room={statusTarget}
          onClose={() => setStatusTarget(null)}
        />
      )}
    </div>
  )
}
