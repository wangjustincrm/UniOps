import { useRef, useState, type ChangeEvent } from 'react'
import { Paperclip, Upload, X, Eye, Download, Loader2, ScanLine } from 'lucide-react'
import { ATTACHMENT_RULE, partitionUploads } from '@/lib/uploadLimits'
import { formatBytes, formatDate } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  useDeleteShipmentAttachment, useShipmentAttachments, useUploadShipmentAttachment,
} from '@/hooks/useFreight'
import { shipmentAttachmentService, type ShipmentAttachment } from '@/services/freight'
import { errorMessage } from './freightUi'

export function ShippingLabelBadge() {
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-primary-50 px-2 py-0.5 text-[10px] font-medium text-primary-700">
      <ScanLine className="h-3 w-3" /> Shipping label
    </span>
  )
}

function AddFilesButton({ onFiles, disabled, busy, label = 'Add files' }: {
  onFiles: (files: File[]) => void
  disabled?: boolean
  busy?: boolean
  label?: string
}) {
  const input = useRef<HTMLInputElement>(null)
  return (
    <>
      <input
        ref={input} type="file" multiple accept={ATTACHMENT_RULE.accept} className="sr-only"
        onChange={(e: ChangeEvent<HTMLInputElement>) => {
          const files = Array.from(e.target.files ?? [])
          e.target.value = ''
          if (files.length) onFiles(files)
        }}
      />
      <Button type="button" variant="secondary" size="sm" className="gap-1.5"
        disabled={disabled || busy} onClick={() => input.current?.click()}>
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
        {busy ? 'Uploading…' : label}
      </Button>
    </>
  )
}

// ─── Saved shipment: files are uploaded / removed immediately ────────────────

/**
 * The Attachments row of a saved shipment (Edit dialog and the View page).
 * `editable` = the caller may change it (the server refuses a voided shipment
 * anyway); everyone who can open the shipment can view and download.
 */
export function ShipmentAttachmentsPanel({ shipmentId, editable }: { shipmentId: string; editable: boolean }) {
  const { data: atts = [], isPending } = useShipmentAttachments(shipmentId)
  const upload = useUploadShipmentAttachment(shipmentId)
  const remove = useDeleteShipmentAttachment(shipmentId)
  const [error, setError] = useState<string | null>(null)

  const addFiles = async (files: File[]) => {
    if (upload.isPending) return
    const { ok, problems } = partitionUploads(files, ATTACHMENT_RULE)
    setError(problems.length ? problems.join(' ') : null)
    // One at a time, so the first failure stops the batch and is named.
    for (const file of ok) {
      try {
        await upload.mutateAsync({ file })
      } catch (e) {
        setError(`${file.name}: ${errorMessage(e, 'upload failed')}`)
        return
      }
    }
  }

  const handleDelete = (a: ShipmentAttachment) => {
    if (remove.isPending) return
    if (!window.confirm(`Remove ${a.filename} from this shipment?`)) return
    setError(null)
    remove.mutate(a.id, { onError: (e) => setError(errorMessage(e, 'Could not remove the file')) })
  }

  const run = (p: Promise<void>) => p.catch((e) => setError(errorMessage(e, 'Could not open the file')))

  return (
    <div className="flex flex-col gap-2">
      {isPending ? (
        <p className="text-xs text-neutral-400">Loading attachments…</p>
      ) : atts.length === 0 ? (
        <p className="text-xs text-neutral-400">No attachments.</p>
      ) : atts.map((a) => (
        <div key={a.id} className="flex items-center gap-3 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm">
          <Paperclip className="h-4 w-4 shrink-0 text-neutral-400" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-neutral-700">{a.filename}</span>
              {a.kind === 'waybill' && <ShippingLabelBadge />}
            </div>
            <div className="text-[11px] text-neutral-400">
              {formatBytes(a.file_size)} · {a.uploaded_by_name ?? '—'} · {formatDate(a.created_at)}
            </div>
          </div>
          <button type="button" onClick={() => run(shipmentAttachmentService.open(shipmentId, a.id))}
            className="inline-flex items-center gap-1 text-xs text-primary-600 hover:underline">
            <Eye className="h-3.5 w-3.5" /> View
          </button>
          <button type="button" onClick={() => run(shipmentAttachmentService.download(shipmentId, a))}
            className="inline-flex items-center gap-1 text-xs text-primary-600 hover:underline">
            <Download className="h-3.5 w-3.5" /> Download
          </button>
          {editable && (
            <button type="button" disabled={remove.isPending} onClick={() => handleDelete(a)}
              className="text-neutral-400 hover:text-danger-600 disabled:opacity-50" aria-label={`Remove ${a.filename}`}>
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      ))}

      {editable && (
        <div className="flex items-center gap-3">
          <AddFilesButton onFiles={addFiles} busy={upload.isPending} />
          <span className="text-xs text-neutral-400">{ATTACHMENT_RULE.hint}</span>
        </div>
      )}
      {error && <p className="text-xs text-danger-600">{error}</p>}
    </div>
  )
}

// ─── New shipment: files are held until the shipment is saved ───────────────

/**
 * The Attachments row of New Shipment. Nothing exists to attach to yet, so the
 * scanned label and the extra files are held here and uploaded right after the
 * shipment is created (ShipmentDialog.save).
 */
export function StagedShipmentAttachments({ label, onRemoveLabel, files, onFilesChange, disabled }: {
  label: File | null
  onRemoveLabel: () => void
  files: File[]
  onFilesChange: (files: File[]) => void
  disabled?: boolean
}) {
  const [error, setError] = useState<string | null>(null)

  const addFiles = (picked: File[]) => {
    const { ok, problems } = partitionUploads(picked, ATTACHMENT_RULE)
    setError(problems.length ? problems.join(' ') : null)
    onFilesChange([...files, ...ok])
  }

  const row = (name: string, size: number, isLabel: boolean, onRemove: () => void, key: string) => (
    <div key={key} className="flex items-center gap-3 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm">
      <Paperclip className="h-4 w-4 shrink-0 text-neutral-400" />
      <span className="min-w-0 flex-1 truncate text-neutral-700">{name}</span>
      {isLabel && <ShippingLabelBadge />}
      <span className="text-xs text-neutral-400">{formatBytes(size)}</span>
      <button type="button" disabled={disabled} onClick={onRemove}
        className="text-neutral-400 hover:text-danger-600 disabled:opacity-50" aria-label={`Remove ${name}`}>
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )

  return (
    <div className="flex flex-col gap-2">
      {label && row(label.name, label.size, true, onRemoveLabel, 'label')}
      {files.map((f, i) => row(f.name, f.size, false, () => onFilesChange(files.filter((_, j) => j !== i)), `f${i}`))}
      <div className="flex items-center gap-3">
        <AddFilesButton onFiles={addFiles} disabled={disabled} />
        <span className="text-xs text-neutral-400">
          {label ? ATTACHMENT_RULE.hint : `The scanned waybill is kept here too · ${ATTACHMENT_RULE.hint}`}
        </span>
      </div>
      {error && <p className="text-xs text-danger-600">{error}</p>}
    </div>
  )
}
