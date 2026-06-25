/** Visit attachments — uploader + viewer (W11+W12 S2-E / PRD §6.5.6).
 *
 * Backed by file-api via vms-api's proxy. Auditor sees but cannot upload
 * (server enforces 403 on POST).
 */
import { useRef, useState } from 'react'
import { Paperclip, Upload, Loader2, FileText, AlertCircle } from 'lucide-react'
import {
  useUploadVisitAttachment, useVisitAttachments, type VisitAttachment,
} from '@/services/api'
import { formatDateTime } from '@/lib/utils'

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
    <div className="rounded-lg border border-neutral-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-neutral-500">
          <Paperclip className="h-4 w-4" />
          Attachments
        </div>
        {!readOnly && (
          <>
            <input
              ref={inputRef}
              type="file"
              hidden
              onChange={onChange}
              accept=".pdf,.png,.jpg,.jpeg,.gif,.doc,.docx,.xls,.xlsx"
            />
            <button
              type="button"
              onClick={onPick}
              disabled={upload.isPending}
              className="inline-flex items-center gap-1.5 rounded-md border border-neutral-300 bg-white px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
            >
              {upload.isPending
                ? <Loader2 className="h-3 w-3 animate-spin" />
                : <Upload className="h-3 w-3" />}
              Upload
            </button>
          </>
        )}
      </div>

      {error && (
        <p className="mt-2 flex items-center gap-1 text-xs text-danger-600">
          <AlertCircle className="h-3 w-3" />
          {error}
        </p>
      )}

      {isLoading && (
        <p className="mt-2 text-xs text-neutral-400">Loading…</p>
      )}

      {!isLoading && (data?.length ?? 0) === 0 && (
        <p className="mt-2 text-xs text-neutral-400">No attachments yet.</p>
      )}

      {!!data?.length && (
        <ul className="mt-2 divide-y divide-neutral-100">
          {data.map((att) => (
            <AttachmentRow key={att.id} att={att} />
          ))}
        </ul>
      )}
    </div>
  )
}

function AttachmentRow({ att }: { att: VisitAttachment }) {
  return (
    <li className="flex items-center justify-between gap-3 py-2 text-sm">
      <div className="flex min-w-0 items-center gap-2">
        <FileText className="h-4 w-4 shrink-0 text-neutral-400" />
        <div className="min-w-0">
          <a
            href={att.download_url}
            target="_blank"
            rel="noopener noreferrer"
            className="truncate text-primary-700 hover:underline"
          >
            {att.original_filename}
          </a>
          <p className="text-[11px] text-neutral-400">
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
