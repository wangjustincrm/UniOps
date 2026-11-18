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
import { useConfirmDialog } from '@/components/ui/ConfirmDialog'

import { FileInput, Badge } from '@uniops/shell'
export function ShippingLabelBadge() {
  return (
    <Badge variant="accent" size="sm" className="shrink-0">
      <ScanLine className="h-3 w-3" /> Shipping label
    </Badge>
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
      <FileInput
        ref={input} multiple accept={ATTACHMENT_RULE.accept} className="sr-only"
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
  const confirmDlg = useConfirmDialog()

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
    // Asked in-page (was the browser's confirm); a refusal still shows under
    // the list.
    confirmDlg.ask({
      title: 'Remove File',
      subtitle: a.filename,
      message: `Remove ${a.filename} from this shipment?`,
      confirmLabel: 'Remove',
      onConfirm: () => {
        setError(null)
        remove.mutate(a.id, { onError: (e) => setError(errorMessage(e, 'Could not remove the file')) })
      },
    })
  }

  const run = (p: Promise<void>) => p.catch((e) => setError(errorMessage(e, 'Could not open the file')))

  return (
    <div className="flex flex-col gap-2">
      {isPending ? (
        <p className="text-xs text-fg-muted">Loading attachments…</p>
      ) : atts.length === 0 ? (
        <p className="text-xs text-fg-muted">No attachments.</p>
      ) : atts.map((a) => (
        <div key={a.id} className="flex items-center gap-3 rounded-md border border-line bg-surface-subtle px-3 py-2 text-sm">
          <Paperclip className="h-4 w-4 shrink-0 text-fg-muted" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-fg-secondary">{a.filename}</span>
              {a.kind === 'waybill' && <ShippingLabelBadge />}
            </div>
            <div className="text-2xs text-fg-muted">
              {formatBytes(a.file_size)} · {a.uploaded_by_name ?? '—'} · {formatDate(a.created_at)}
            </div>
          </div>
          <Button variant="link" type="button" onClick={() => run(shipmentAttachmentService.open(shipmentId, a.id))}
            className="text-xs">
            <Eye className="h-3.5 w-3.5" /> View
          </Button>
          <Button variant="link" type="button" onClick={() => run(shipmentAttachmentService.download(shipmentId, a))}
            className="text-xs">
            <Download className="h-3.5 w-3.5" /> Download
          </Button>
          {editable && (
            <Button variant="danger-ghost" size="icon-xs" type="button" disabled={remove.isPending} onClick={() => handleDelete(a)}
              aria-label={`Remove ${a.filename}`}>
              <X className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      ))}

      {editable && (
        <div className="flex items-center gap-3">
          <AddFilesButton onFiles={addFiles} busy={upload.isPending} />
          <span className="text-xs text-fg-muted">{ATTACHMENT_RULE.hint}</span>
        </div>
      )}
      {error && <p className="text-xs text-danger-fg">{error}</p>}
      {confirmDlg.dialog}
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
    <div key={key} className="flex items-center gap-3 rounded-md border border-line bg-surface-subtle px-3 py-2 text-sm">
      <Paperclip className="h-4 w-4 shrink-0 text-fg-muted" />
      <span className="min-w-0 flex-1 truncate text-fg-secondary">{name}</span>
      {isLabel && <ShippingLabelBadge />}
      <span className="text-xs text-fg-muted">{formatBytes(size)}</span>
      <Button variant="danger-ghost" size="icon-xs" type="button" disabled={disabled} onClick={onRemove}
        aria-label={`Remove ${name}`}>
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  )

  return (
    <div className="flex flex-col gap-2">
      {label && row(label.name, label.size, true, onRemoveLabel, 'label')}
      {files.map((f, i) => row(f.name, f.size, false, () => onFilesChange(files.filter((_, j) => j !== i)), `f${i}`))}
      <div className="flex items-center gap-3">
        <AddFilesButton onFiles={addFiles} disabled={disabled} />
        <span className="text-xs text-fg-muted">
          {label ? ATTACHMENT_RULE.hint : `The scanned waybill is kept here too · ${ATTACHMENT_RULE.hint}`}
        </span>
      </div>
      {error && <p className="text-xs text-danger-fg">{error}</p>}
    </div>
  )
}
