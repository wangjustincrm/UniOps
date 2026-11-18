import { useState } from 'react'
import { Upload, Paperclip, X } from 'lucide-react'
import { Button, FileInput, IconButton, Spinner } from '@uniops/shell'
import { ATTACHMENT_RULE, partitionUploads } from '@/lib/uploadLimits'
import { formatBytes } from '@/lib/utils'

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
    // Refused here, with the reason, rather than by the server after the fact.
    const { ok, problems } = partitionUploads(files, ATTACHMENT_RULE)
    setError(problems.length ? problems.join(' ') : null)
    try {
      // Sequential so the first failure stops the batch instead of firing them
      // all off and leaving a partial upload the user can't reason about.
      for (const file of ok) {
        await onUpload(file)
      }
    } catch {
      setError(`Upload failed. Accepted: ${ATTACHMENT_RULE.hint}.`)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <label
        htmlFor={inputId}
        className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed border-line-strong p-6 text-center transition-colors hover:border-accent hover:bg-accent-soft"
      >
        {isUploading ? <Spinner className="h-8 w-8" label="Uploading" /> : <Upload className="h-8 w-8 text-fg-muted" />}
        <div>
          <p className="text-sm font-medium text-fg-secondary">
            {isUploading ? 'Uploading…' : 'Drag & drop or click to upload'}
          </p>
          <p className="text-xs text-fg-muted mt-1">{ATTACHMENT_RULE.hint}</p>
        </div>
        <FileInput
          id={inputId}
          multiple
          accept={ATTACHMENT_RULE.accept}
          className="sr-only"
          disabled={isUploading}
          onChange={handleFileInput}
        />
      </label>

      {error && <p className="text-xs text-danger-fg">{error}</p>}

      {attachments.map((att) => (
        <div
          key={att.id}
          className="flex items-center gap-3 rounded-md border border-line bg-surface-subtle px-3 py-2 text-sm"
        >
          <Paperclip className="h-4 w-4 shrink-0 text-fg-muted" />
          <span className="flex-1 truncate text-fg-secondary">{att.filename}</span>
          <span className="text-xs text-fg-muted">{formatBytes(att.file_size)}</span>
          <Button type="button" variant="link" className="text-xs" onClick={() => onDownload(att)}>
            Download
          </Button>
          <IconButton
            type="button"
            label={`Remove ${att.filename}`}
            icon={<X className="h-3.5 w-3.5" />}
            size="icon-xs"
            disabled={isDeleting}
            onClick={() => { setError(null); onDelete(att.id) }}
          />
        </div>
      ))}
    </div>
  )
}
