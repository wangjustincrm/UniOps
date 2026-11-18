/**
 * RoomImportModal — upload an xlsx file to bulk-import rooms.
 *
 * Expected columns: name, code, campus, building, floor, area, capacity,
 * equipment (comma-separated), room_type, open_time_start, open_time_end
 */
import { useState, useRef } from 'react'
import { Upload, Loader2, AlertCircle, CheckCircle2, FileSpreadsheet } from 'lucide-react'
import { useAdminImportRooms } from '@/services/api'

import { Button, ChoiceCard, FileInput, Modal } from '@uniops/shell'
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
    <Modal
      open
      onClose={onClose}
      title="Import Rooms from Excel"
      // Closes by Cancel / the close button only, as before.
      closeOnEscape={false}
      size="full"
      className="max-w-lg"
      bodyClassName="p-0"
    >
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
            <FileInput
              ref={inputRef}
             
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              hidden
              onChange={onFilePick}
            />
            <ChoiceCard
              onClick={() => inputRef.current?.click()}
              className="w-full items-center border-2 border-dashed border-line-strong px-4 py-6 text-sm text-fg-muted hover:border-accent hover:text-accent-fg"
            >
              <FileSpreadsheet className="h-8 w-8 text-fg-faint" />
              {file ? (
                <span className="font-medium text-fg-secondary">{file.name}</span>
              ) : (
                <span>Click to select an .xlsx file</span>
              )}
            </ChoiceCard>
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
          <Button variant="secondary" size="sm"
            type="button"
            onClick={onClose}
           
          >
            Close
          </Button>
          <Button size="sm"
            type="button"
            onClick={handleUpload}
            disabled={!file || importMut.isPending}
           
          >
            {importMut.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Upload className="h-3.5 w-3.5" />
            )}
            Upload
          </Button>
        </div>
    </Modal>
  )

  return modal
}
