/** Visit attachments — uploader + viewer (W11+W12 S2-E / PRD §6.5.6).
 *
 * Backed by file-api via vms-api's proxy. Auditor sees but cannot upload
 * (server enforces 403 on POST).
 */
import { useRef, useState } from 'react'
import { Paperclip, Upload, Loader2, FileText, AlertCircle } from 'lucide-react'
import {
  useUploadVisitAttachment, useVisitAttachments, type VisitAttachment,
  openAttachment,
} from '@/services/api'
import { formatDateTime } from '@/lib/utils'

import { FileInput, Button, Card } from '@uniops/shell'
interface Props {
  visitId: string
  readOnly?: boolean
}

export function VisitAttachments({ visitId, readOnly }: Props) {
  const { data, isLoading } = useVisitAttachments(visitId)
  const upload = useUploadVisitAttachment(visitId)
  const inputRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)

  const onPick = () => inputRef.current?.click()

  const onChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setError(null)
    upload.mutate(file, {
      onError: (err) => setError(err.message),
    })
  }

  return (
    <Card inset className="p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-fg-muted">
          <Paperclip className="h-4 w-4" />
          Attachments
        </div>
        {!readOnly && (
          <>
            <FileInput
              ref={inputRef}
             
              hidden
              onChange={onChange}
              accept=".pdf,.png,.jpg,.jpeg,.gif,.doc,.docx,.xls,.xlsx"
            />
            <Button variant="secondary" size="xs"
              type="button"
              onClick={onPick}
              disabled={upload.isPending}
             
            >
              {upload.isPending
                ? <Loader2 className="h-3 w-3 animate-spin" />
                : <Upload className="h-3 w-3" />}
              Upload
            </Button>
          </>
        )}
      </div>

      {error && (
        <p className="mt-2 flex items-center gap-1 text-xs text-danger-fg">
          <AlertCircle className="h-3 w-3" />
          {error}
        </p>
      )}

      {isLoading && (
        <p className="mt-2 text-xs text-fg-muted">Loading…</p>
      )}

      {!isLoading && (data?.length ?? 0) === 0 && (
        <p className="mt-2 text-xs text-fg-muted">No attachments yet.</p>
      )}

      {!!data?.length && (
        <ul className="mt-2 divide-y divide-line-subtle">
          {data.map((att) => (
            <AttachmentRow key={att.id} att={att} />
          ))}
        </ul>
      )}
    </Card>
  )
}

function AttachmentRow({ att }: { att: VisitAttachment }) {
  return (
    <li className="flex items-center justify-between gap-3 py-2 text-sm">
      <div className="flex min-w-0 items-center gap-2">
        <FileText className="h-4 w-4 shrink-0 text-fg-muted" />
        <div className="min-w-0">
          <Button variant="link"
            type="button"
            onClick={() => openAttachment(att).catch((e: Error) => alert(`Could not open the file: ${e.message}`))}
            className="truncate text-left"
          >
            {att.original_filename}
          </Button>
          <p className="text-2xs text-fg-muted">
            {formatBytes(att.file_size)} ·{' '}
            {att.created_at ? formatDateTime(att.created_at) : 'unknown date'}
          </p>
        </div>
      </div>
    </li>
  )
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
