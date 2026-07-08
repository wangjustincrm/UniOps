/**
 * RoomFormModal — create / edit a meeting room.
 *
 * Image upload is wired to file-api (Task 18): selecting a file immediately
 * uploads it and appends the returned id to image_file_ids. Existing ids are
 * preserved on edit so removing and re-saving does not wipe existing images.
 */
import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { Loader2, X as XIcon, AlertCircle, ImagePlus, Trash2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  useAdminCreateRoom,
  useAdminUpdateRoom,
} from '@/services/api'
import type { RoomOut, RoomCreate, RoomUpdate } from '@/lib/types'
import {
  EQUIPMENT_OPTIONS,
  EQUIPMENT_LABELS,
  ROOM_TYPES,
  ROOM_TYPE_LABELS,
} from '@/lib/types'
import { ApiError, getToken } from '@/lib/api'

// ── File-api upload helper ────────────────────────────────────────────────────

const FILE_API_BASE = (import.meta.env.VITE_FILE_API_URL as string | undefined) ?? ''

interface UploadedFile {
  id: string
  original_filename: string
}

/**
 * Upload a single image to file-api (multipart POST) and return the file id.
 * Mirrors the vms attachment pattern: multipart fetch with Bearer token.
 */
async function uploadRoomImage(file: File): Promise<string> {
  if (!FILE_API_BASE) {
    throw new Error('File API URL not configured (VITE_FILE_API_URL is unset).')
  }
  const token = getToken()
  const fd = new FormData()
  fd.append('file', file)
  const resp = await fetch(`${FILE_API_BASE}/files/v1/files`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail ?? `Upload failed: HTTP ${resp.status}`)
  }
  const data: UploadedFile = await resp.json()
  return data.id
}

interface Props {
  room?: RoomOut | null
  onClose: () => void
  onSaved: (room: RoomOut) => void
}

const EMPTY_FORM: RoomCreate = {
  name: '',
  code: '',
  campus: '',
  building: '',
  floor: '',
  area: '',
  capacity: 1,
  equipment: [],
  room_type: 'standard',
  open_time_start: '',
  open_time_end: '',
  advance_booking_days: undefined,
  owner_department: '',
  notes: '',
  image_file_ids: [],
}

function roomToForm(room: RoomOut): RoomCreate {
  return {
    name: room.name,
    code: room.code,
    campus: room.campus ?? '',
    building: room.building ?? '',
    floor: room.floor ?? '',
    area: room.area ?? '',
    capacity: room.capacity,
    equipment: room.equipment,
    room_type: room.room_type,
    open_time_start: room.open_time_start?.slice(0, 5) ?? '',
    open_time_end: room.open_time_end?.slice(0, 5) ?? '',
    advance_booking_days: room.advance_booking_days ?? undefined,
    owner_department: room.owner_department ?? '',
    notes: room.notes ?? '',
    image_file_ids: room.image_file_ids,
  }
}

function nullify(v: string | undefined): string | null {
  return v && v.trim() ? v.trim() : null
}

export function RoomFormModal({ room, onClose, onSaved }: Props) {
  const isEdit = !!room
  const [form, setForm] = useState<RoomCreate>(room ? roomToForm(room) : EMPTY_FORM)
  const [error, setError] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setForm(room ? roomToForm(room) : EMPTY_FORM)
    setError(null)
    setUploadError(null)
  }, [room])

  const createMut = useAdminCreateRoom()
  const updateMut = useAdminUpdateRoom(room?.id ?? '')

  const isPending = createMut.isPending || updateMut.isPending

  function setField<K extends keyof RoomCreate>(key: K, value: RoomCreate[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function toggleEquipment(item: string) {
    setForm((prev) => ({
      ...prev,
      equipment: prev.equipment?.includes(item)
        ? (prev.equipment ?? []).filter((e) => e !== item)
        : [...(prev.equipment ?? []), item],
    }))
  }

  async function handleImageSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? [])
    if (files.length === 0) return
    // Reset the input so the same file can be re-selected if needed
    if (fileInputRef.current) fileInputRef.current.value = ''
    setUploadError(null)
    setUploading(true)
    try {
      const ids = await Promise.all(files.map(uploadRoomImage))
      // MUST append — never replace — so existing room images are preserved on edit.
      setForm((prev) => ({
        ...prev,
        image_file_ids: [...(prev.image_file_ids ?? []), ...ids],
      }))
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Image upload failed.')
    } finally {
      setUploading(false)
    }
  }

  function removeImage(id: string) {
    setForm((prev) => ({
      ...prev,
      image_file_ids: (prev.image_file_ids ?? []).filter((fid) => fid !== id),
    }))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    const payload: RoomCreate = {
      ...form,
      campus: nullify(form.campus as string | undefined),
      building: nullify(form.building as string | undefined),
      floor: nullify(form.floor as string | undefined),
      area: nullify(form.area as string | undefined),
      open_time_start: nullify(form.open_time_start as string | undefined),
      open_time_end: nullify(form.open_time_end as string | undefined),
      owner_department: nullify(form.owner_department as string | undefined),
      notes: nullify(form.notes as string | undefined),
      advance_booking_days: form.advance_booking_days || undefined,
    }

    try {
      if (isEdit && room) {
        const updatePayload: RoomUpdate = { ...payload }
        const updated = await updateMut.mutateAsync(updatePayload)
        onSaved(updated)
      } else {
        const created = await createMut.mutateAsync(payload)
        onSaved(created)
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError('Room code already exists. Choose a different code.')
      } else if (err instanceof Error) {
        setError(err.message)
      } else {
        setError('An unexpected error occurred.')
      }
    }
  }

  const modal = (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-2xl rounded-xl border border-neutral-200 bg-white shadow-xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-neutral-200 shrink-0">
          <h2 className="text-base font-semibold text-neutral-900">
            {isEdit ? 'Edit Room' : 'New Room'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-neutral-400 hover:text-neutral-600"
          >
            <XIcon className="h-5 w-5" />
          </button>
        </div>

        {/* Scrollable body */}
        <form id="room-form" onSubmit={handleSubmit} className="overflow-y-auto flex-1 px-6 py-4 space-y-4">
          {error && (
            <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          )}

          {/* Name + Code */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-neutral-700 mb-1">Name <span className="text-red-500">*</span></label>
              <input
                required
                value={form.name}
                onChange={(e) => setField('name', e.target.value)}
                className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
                placeholder="Conference Room A"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-neutral-700 mb-1">Code <span className="text-red-500">*</span></label>
              <input
                required
                value={form.code}
                onChange={(e) => setField('code', e.target.value.toUpperCase())}
                className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
                placeholder="CR-A"
              />
            </div>
          </div>

          {/* Location */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-neutral-700 mb-1">Campus</label>
              <input
                value={form.campus as string ?? ''}
                onChange={(e) => setField('campus', e.target.value)}
                className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
                placeholder="Main Campus"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-neutral-700 mb-1">Building</label>
              <input
                value={form.building as string ?? ''}
                onChange={(e) => setField('building', e.target.value)}
                className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
                placeholder="Building 1"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-neutral-700 mb-1">Floor</label>
              <input
                value={form.floor as string ?? ''}
                onChange={(e) => setField('floor', e.target.value)}
                className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
                placeholder="2"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-neutral-700 mb-1">Area</label>
              <input
                value={form.area as string ?? ''}
                onChange={(e) => setField('area', e.target.value)}
                className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
                placeholder="East Wing"
              />
            </div>
          </div>

          {/* Capacity + Type */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-neutral-700 mb-1">Capacity <span className="text-red-500">*</span></label>
              <input
                required
                type="number"
                min={1}
                value={form.capacity}
                onChange={(e) => setField('capacity', Number(e.target.value))}
                className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-neutral-700 mb-1">Room Type</label>
              <select
                value={form.room_type}
                onChange={(e) => setField('room_type', e.target.value)}
                className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              >
                {ROOM_TYPES.map((t) => (
                  <option key={t} value={t}>{ROOM_TYPE_LABELS[t]}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Equipment */}
          <div>
            <label className="block text-xs font-medium text-neutral-700 mb-1">Equipment</label>
            <div className="flex flex-wrap gap-2">
              {EQUIPMENT_OPTIONS.map((item) => {
                const checked = (form.equipment ?? []).includes(item)
                return (
                  <label
                    key={item}
                    className={cn(
                      'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs cursor-pointer transition-colors',
                      checked
                        ? 'border-[#085E5E] bg-[#085E5E]/10 text-[#085E5E] font-medium'
                        : 'border-neutral-300 bg-white text-neutral-600 hover:border-neutral-400',
                    )}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={checked}
                      onChange={() => toggleEquipment(item)}
                    />
                    {EQUIPMENT_LABELS[item]}
                  </label>
                )
              })}
            </div>
          </div>

          {/* Open hours */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-neutral-700 mb-1">Open Time Start</label>
              <input
                type="time"
                value={form.open_time_start as string ?? ''}
                onChange={(e) => setField('open_time_start', e.target.value)}
                className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-neutral-700 mb-1">Open Time End</label>
              <input
                type="time"
                value={form.open_time_end as string ?? ''}
                onChange={(e) => setField('open_time_end', e.target.value)}
                className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              />
            </div>
          </div>

          {/* Advance booking days */}
          <div>
            <label className="block text-xs font-medium text-neutral-700 mb-1">Advance Booking Days</label>
            <input
              type="number"
              min={0}
              value={form.advance_booking_days ?? ''}
              onChange={(e) => setField('advance_booking_days', e.target.value ? Number(e.target.value) : undefined)}
              className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              placeholder="Leave blank to use system default"
            />
          </div>

          {/* Owner department */}
          <div>
            <label className="block text-xs font-medium text-neutral-700 mb-1">Owner Department</label>
            <input
              value={form.owner_department as string ?? ''}
              onChange={(e) => setField('owner_department', e.target.value)}
              className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
              placeholder="e.g. Facilities"
            />
          </div>

          {/* Notes */}
          <div>
            <label className="block text-xs font-medium text-neutral-700 mb-1">Notes</label>
            <textarea
              rows={2}
              value={form.notes as string ?? ''}
              onChange={(e) => setField('notes', e.target.value)}
              className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40 resize-none"
              placeholder="Optional notes visible to admins"
            />
          </div>

          {/* Image upload — file-api */}
          <div>
            <label className="block text-xs font-medium text-neutral-700 mb-1">Room Images</label>

            {/* Thumbnail grid with per-image remove */}
            {(form.image_file_ids ?? []).length > 0 && FILE_API_BASE && (
              <div className="flex flex-wrap gap-2 mb-2">
                {(form.image_file_ids ?? []).map((fid) => (
                  <div key={fid} className="relative group">
                    <img
                      src={`${FILE_API_BASE}/files/v1/files/${fid}`}
                      alt="Room image"
                      className="h-20 w-28 rounded-lg object-cover border border-neutral-200"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                    />
                    <button
                      type="button"
                      onClick={() => removeImage(fid)}
                      className="absolute top-1 right-1 hidden group-hover:flex items-center justify-center h-5 w-5 rounded-full bg-red-500 text-white shadow"
                      title="Remove image"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Upload button */}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              className="sr-only"
              onChange={handleImageSelect}
              disabled={uploading || !FILE_API_BASE}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading || !FILE_API_BASE}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-md border border-neutral-300 px-3 py-1.5 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50 disabled:cursor-not-allowed',
              )}
            >
              {uploading
                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : <ImagePlus className="h-3.5 w-3.5" />
              }
              {uploading ? 'Uploading…' : 'Add Images'}
            </button>

            {!FILE_API_BASE && (
              <p className="mt-1 text-xs text-neutral-400">
                Image upload requires VITE_FILE_API_URL to be configured.
              </p>
            )}
            {uploadError && (
              <div className="mt-1 flex items-center gap-1.5 text-xs text-red-600">
                <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                {uploadError}
              </div>
            )}
          </div>
        </form>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-6 py-4 border-t border-neutral-200 shrink-0">
          <button
            type="button"
            onClick={onClose}
            disabled={isPending}
            className="rounded-md border border-neutral-300 px-4 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            form="room-form"
            disabled={isPending}
            onClick={handleSubmit}
            className="inline-flex items-center gap-1.5 rounded-md bg-[#085E5E] px-4 py-1.5 text-sm font-semibold text-white hover:bg-[#085E5E]/90 disabled:opacity-50"
          >
            {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {isEdit ? 'Save Changes' : 'Create Room'}
          </button>
        </div>
      </div>
    </div>
  )

  return createPortal(modal, document.body)
}
