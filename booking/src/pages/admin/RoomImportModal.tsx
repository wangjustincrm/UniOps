/**
 * RoomImportModal — upload an xlsx file to bulk-import rooms.
 *
 * Expected columns: name, code, campus, building, floor, area, capacity,
 * equipment (comma-separated), room_type, open_time_start, open_time_end
 */
import { useState, useRef } from 'react'
import { createPortal } from 'react-dom'
import { Upload, Loader2, X as XIcon, AlertCircle, CheckCircle2, FileSpreadsheet } from 'lucide-react'
import { useAdminImportRooms } from '@/services/api'

interface Props {
  onClose: () => void
}

const EXPECTED_COLUMNS = [
  'name',
  'code',
  'floor',
  'area',
  'capacity',
  'equipment (comma-separated)',
  'room_type',
  'open_time_start',
  'open_time_end',
]

export function RoomImportModal({ onClose }: Props) {
  const importMut = useAdminImportRooms()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<{ created: number; errors: Array<{ row: number; message: string }> } | null>(null)

  function onFilePick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null
    e.target.value = ''
    setFile(f)
    setResult(null)
  }

  async function handleUpload() {
    if (!file) return
    setResult(null)
    try {
      const res = await importMut.mutateAsync(file)
      setResult(res)
    } catch {
      // error surfaced via importMut.error
    }
  }

  const modal = (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl border border-line bg-surface shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-line">
          <h2 className="text-base font-semibold text-fg">Import Rooms from Excel</h2>
          <button type="button" onClick={onClose} className="text-fg-muted hover:text-fg-secondary">
            <XIcon className="h-5 w-5" />
          </button>
        </div>

        <div className="px-6 py-4 space-y-4">
          {/* Template hint */}
          <div className="rounded-md border border-line bg-surface-subtle p-3">
            <p className="text-xs font-semibold text-fg-secondary mb-1.5">Expected columns (in any order):</p>
            <div className="flex flex-wrap gap-1.5">
              {EXPECTED_COLUMNS.map((col) => (
                <span key={col} className="inline-flex items-center rounded-md bg-surface border border-line px-2 py-0.5 text-xs text-fg-secondary font-mono">
                  {col}
                </span>
              ))}
            </div>
            <p className="mt-2 text-xs text-fg-muted">
              <strong>room_type</strong> must be one of: standard, training, boardroom, multi_function.
              Times in HH:MM format. <strong>capacity</strong> must be a positive integer.
            </p>
          </div>

          {/* File picker */}
          <div>
            <input
              ref={inputRef}
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              hidden
              onChange={onFilePick}
            />
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className="w-full flex flex-col items-center gap-2 rounded-lg border-2 border-dashed border-line-strong px-4 py-6 text-sm text-fg-muted hover:border-primary-700/40 hover:text-primary-700 transition-colors"
            >
              <FileSpreadsheet className="h-8 w-8 text-fg-faint" />
              {file ? (
                <span className="font-medium text-fg-secondary">{file.name}</span>
              ) : (
                <span>Click to select an .xlsx file</span>
              )}
            </button>
          </div>

          {/* Error from mutation */}
          {importMut.error && (
            <div className="flex items-center gap-2 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {importMut.error.message}
            </div>
          )}

          {/* Result */}
          {result && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 rounded-md border border-success-line bg-success-soft px-3 py-2 text-sm text-success-fg">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                {result.created} room{result.created !== 1 ? 's' : ''} imported successfully.
              </div>
              {result.errors.length > 0 && (
                <div className="rounded-md border border-danger-line bg-danger-soft p-3">
                  <p className="text-xs font-semibold text-danger-fg mb-1.5">
                    {result.errors.length} row{result.errors.length !== 1 ? 's' : ''} failed:
                  </p>
                  <ul className="space-y-0.5 max-h-40 overflow-y-auto">
                    {result.errors.map((e, i) => (
                      <li key={i} className="text-xs text-danger-fg">
                        Row {e.row}: {e.message}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-6 py-4 border-t border-line">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-line-strong px-4 py-1.5 text-sm text-fg-secondary hover:bg-surface-subtle"
          >
            Close
          </button>
          <button
            type="button"
            onClick={handleUpload}
            disabled={!file || importMut.isPending}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary-700 px-4 py-1.5 text-sm font-semibold text-white hover:bg-primary-700/90 disabled:opacity-50"
          >
            {importMut.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Upload className="h-3.5 w-3.5" />
            )}
            Upload
          </button>
        </div>
      </div>
    </div>
  )

  return createPortal(modal, document.body)
}
