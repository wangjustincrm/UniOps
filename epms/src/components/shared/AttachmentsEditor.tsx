import { useState } from 'react'
import { Upload, Paperclip, X } from 'lucide-react'

export interface EditableAttachment {
  id: string
  filename: string
  file_size: number
}

interface AttachmentsEditorProps {
  attachments: EditableAttachment[]
  /** Uploads one file to the already-saved document; rejects on failure. */
  onUpload: (file: File) => Promise<unknown>
  onDelete: (attId: string) => void
  onDownload: (att: EditableAttachment) => void
  isUploading?: boolean
  isDeleting?: boolean
  /** Must be unique on the page — the drop zone label targets it. */
  inputId: string
}

/**
 * Attachment list + drop zone for a document that already exists on the server.
 *
 * Uploads and deletes are applied immediately rather than staged until Save:
 * the parent already has an id to hang files off, and this matches how the
 * Detail pages have always deleted. The Create pages keep their own staged-file
 * list because there is no document to attach to yet.
 */
export function AttachmentsEditor({
  attachments, onUpload, onDelete, onDownload,
  isUploading = false, isDeleting = false, inputId,
}: AttachmentsEditorProps) {
  const [error, setError] = useState<string | null>(null)

  const handleFileInput = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    e.target.value = ''
    if (files.length === 0) return
    setError(null)
    try {
      // Sequential so the first failure stops the batch instead of firing them
      // all off and leaving a partial upload the user can't reason about.
      for (const file of files) {
        await onUpload(file)
      }
    } catch {
      setError('Upload failed. Each file must be 25 MB or smaller.')
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <label
        htmlFor={inputId}
        className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed border-neutral-300 p-6 text-center hover:border-primary-400 hover:bg-primary-50 transition-colors"
      >
        <Upload className={`h-8 w-8 text-neutral-400 ${isUploading ? 'animate-pulse' : ''}`} />
        <div>
          <p className="text-sm font-medium text-neutral-700">
            {isUploading ? 'Uploading…' : 'Drag & drop or click to upload'}
          </p>
          <p className="text-xs text-neutral-400 mt-1">Any format · Max 25 MB per file</p>
        </div>
        <input
          id={inputId}
          type="file"
          multiple
          className="sr-only"
          disabled={isUploading}
          onChange={handleFileInput}
        />
      </label>

      {error && <p className="text-xs text-danger-600">{error}</p>}

      {attachments.map((att) => (
        <div
          key={att.id}
          className="flex items-center gap-3 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm"
        >
          <Paperclip className="h-4 w-4 shrink-0 text-neutral-400" />
          <span className="flex-1 truncate text-neutral-700">{att.filename}</span>
          <span className="text-xs text-neutral-400">{(att.file_size / 1024 / 1024).toFixed(1)} MB</span>
          <button
            type="button"
            onClick={() => onDownload(att)}
            className="text-xs text-primary-600 hover:underline"
          >
            Download
          </button>
          <button
            type="button"
            disabled={isDeleting}
            onClick={() => { setError(null); onDelete(att.id) }}
            className="text-neutral-400 hover:text-danger-600 disabled:opacity-50"
            aria-label={`Remove ${att.filename}`}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  )
}
