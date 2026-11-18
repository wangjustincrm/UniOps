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
import { SortTh, useSortableRows, Input, Select, TBody, THead, Table, Td, Textarea, Th, Tr, Button, Modal } from '@uniops/shell'
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
    <Modal
      open
      onClose={onClose}
      busy={mut.isPending}
      title="Change Room Status"
      // Closes by Close / Cancel / the close button only, as before.
      closeOnEscape={false}
      size="full"
      className="max-w-sm"
    >
      <div className="space-y-4">
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
                <Button variant="link"
                  type="button"
                  onClick={() => navigate(`/admin/bookings?room_id=${room.id}`)}
                 
                >
                  All Bookings
                </Button>.
              </span>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm"
                type="button"
                onClick={onClose}
               
              >
                Close
              </Button>
              <Button size="sm"
                type="button"
                onClick={() => navigate(`/admin/bookings?room_id=${room.id}`)}
               
              >
                View Bookings
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div>
              <label className="block text-xs font-medium text-fg-secondary mb-1">New Status</label>
              <Select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="w-full"
              >
                <option value="available">Available</option>
                <option value="disabled">Disabled</option>
                <option value="maintenance">Maintenance</option>
              </Select>
            </div>
            <div>
              <label className="block text-xs font-medium text-fg-secondary mb-1">
                Notes {status === 'maintenance' && <span className="text-danger-fg">*</span>}
              </label>
              <Textarea
                rows={2}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                required={status === 'maintenance'}
                className="w-full resize-none"
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
              <Button variant="secondary" size="sm"
                type="button"
                onClick={onClose}
                disabled={mut.isPending}
               
              >
                Cancel
              </Button>
              <Button size="sm"
                type="button"
                onClick={handleApply}
                disabled={mut.isPending || (status === 'maintenance' && !notes.trim())}
               
              >
                {mut.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Apply
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  )

  return modal
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

  const thCls = 'px-4 py-3 text-xs font-semibold text-fg-muted text-left'
  const tdCls = 'px-4 py-3 text-sm'

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-fg">Rooms</h1>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm"
              type="button"
              onClick={() => setShowImport(true)}
             
            >
              <Upload className="h-4 w-4" />
              Import
            </Button>
            <Button size="sm"
              type="button"
              onClick={() => setFormTarget(null)}
             
            >
              <Plus className="h-4 w-4" />
              New Room
            </Button>
          </div>
        </div>

        {/* Success note */}
        {successNote && (
          <div className="flex items-center justify-between rounded-md border border-success-line bg-success-soft px-4 py-2.5 text-sm text-success-fg">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4" />
              {successNote}
            </div>
            <Button variant="subtle" size="icon-xs" aria-label="Clear" type="button" onClick={() => setSuccessNote(null)}>
              <XIcon className="h-4 w-4 text-success-fg" />
            </Button>
          </div>
        )}

        {/* Filter bar */}
        <div className="flex flex-wrap gap-3">
          <Input
            value={filters.floor}
            onChange={(e) => setFilters((f) => ({ ...f, floor: e.target.value }))}
            placeholder="Floor…"
            className="w-32"
          />
          <Input
            value={filters.area}
            onChange={(e) => setFilters((f) => ({ ...f, area: e.target.value }))}
            placeholder="Area…"
            className="w-36"
          />
          <Select
            value={filters.status}
            onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
           
          >
            <option value="">All Statuses</option>
            <option value="available">Available</option>
            <option value="disabled">Disabled</option>
            <option value="maintenance">Maintenance</option>
          </Select>
          {(filters.floor || filters.area || filters.status) && (
            <Button variant="subtle" size="xs"
              type="button"
              onClick={() => setFilters({ floor: '', area: '', status: '' })}
             
            >
              Clear filters
            </Button>
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
            <Table className="text-left">
              <THead>
                <Tr>
                  <SortTh label="Code" sortKey="code" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <SortTh label="Name" sortKey="name" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <SortTh label="Location" sortKey="location" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <SortTh label="Cap." sortKey="capacity" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <SortTh label="Type" sortKey="type" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <SortTh label="Status" sortKey="status" sort={sorted.sort} onSort={sorted.onSort} className={thCls} />
                  <Th className={thCls}>Actions</Th>
                </Tr>
              </THead>
              <TBody>
                {(rooms ?? []).length === 0 ? (
                  <Tr>
                    <Td align="center" colSpan={7} className="text-sm text-fg-muted">
                      No rooms found. Add one or adjust filters.
                    </Td>
                  </Tr>
                ) : (
                  sorted.rows.map((room) => (
                    <Tr
                      key={room.id}
                      className="hover:bg-surface-subtle transition-colors"
                    >
                      <Td className={cn(tdCls, 'font-mono text-xs text-fg-secondary')}>{room.code}</Td>
                      <Td className={cn(tdCls, 'font-medium text-fg')}>{room.name}</Td>
                      <Td className={cn(tdCls, 'text-fg-muted max-w-[180px] truncate')} title={locationStr(room)}>
                        {locationStr(room) || '—'}
                      </Td>
                      <Td className={tdCls}>{room.capacity}</Td>
                      <Td className={tdCls}>
                        {ROOM_TYPE_LABELS[room.room_type as RoomType] ?? room.room_type}
                      </Td>
                      <Td className={tdCls}>
                        <RoomStatusBadge status={room.status} />
                      </Td>
                      <Td className={tdCls}>
                        <div className="flex items-center gap-1.5">
                          <Button variant="outline" size="xs"
                            type="button"
                            onClick={() => setFormTarget(room)}
                           
                          >
                            <Pencil className="h-3 w-3" />
                            Edit
                          </Button>
                          <Button variant="warning-outline" size="xs"
                            type="button"
                            onClick={() => setStatusTarget(room)}
                           
                          >
                            <ShieldAlert className="h-3 w-3" />
                            Status
                          </Button>
                        </div>
                      </Td>
                    </Tr>
                  ))
                )}
              </TBody>
            </Table>
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
