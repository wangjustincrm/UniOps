// Generic confirm dialog — portal to body (overlay convention, see
// booking/src/pages/admin/RoomImportModal.tsx). Used for "Confirm version"
// (summarise what's being locked) and the import wizard's apply step.
import { createPortal } from 'react-dom'
import { AlertTriangle, Loader2, X as XIcon } from 'lucide-react'
import { Button } from '@uniops/shell'
import { cn } from '@/lib/utils'

export interface ConfirmDialogProps {
  title: string
  children: React.ReactNode
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
  busy?: boolean
  danger?: boolean
}

export function ConfirmDialog({
  title, children, confirmLabel, onConfirm, onCancel, busy = false, danger = false,
}: ConfirmDialogProps) {
  return createPortal(
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/40 p-4">
      <div role="alertdialog" aria-label={title} className="w-full max-w-md rounded-xl border border-neutral-200 bg-white shadow-xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-neutral-200">
          <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-900">
            {danger && <AlertTriangle className="h-4 w-4 text-warning-600" />}
            {title}
          </h2>
          <button type="button" onClick={onCancel} aria-label="Close" className="text-neutral-400 hover:text-neutral-600">
            <XIcon className="h-5 w-5" />
          </button>
        </div>
        <div className="px-5 py-4 text-sm text-neutral-700 space-y-2">
          {children}
        </div>
        <div className="flex justify-end gap-2 px-5 py-4 border-t border-neutral-200">
          <Button type="button" variant="secondary" size="sm" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            variant={danger ? 'destructive' : 'primary'}
            onClick={onConfirm}
            disabled={busy}
            className={cn(busy && 'cursor-wait')}
          >
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
