import { createPortal } from 'react-dom'
import { AlertTriangle, X } from 'lucide-react'

/** Destructive-action confirmation. Same portal + card treatment as ActionModal,
 *  minus the comment box — a hard delete records no reason anywhere. */
export function ConfirmDialog({
  title, message, confirmLabel = 'Delete', onConfirm, onClose, loading, error,
}: {
  title: string
  message: string
  confirmLabel?: string
  onConfirm: () => void
  onClose: () => void
  loading: boolean
  error?: string
}) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-inverse/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-line-subtle px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-danger-soft">
              <AlertTriangle className="h-5 w-5 text-danger-fg" />
            </div>
            <h2 className="text-sm font-semibold text-fg">{title}</h2>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-fg-muted hover:bg-surface-sunken">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <p className="text-sm text-fg-secondary">{message}</p>
          {error && (
            <div className="flex items-center gap-2 rounded-lg bg-danger-soft border border-danger-line px-3 py-2 text-sm text-danger-fg">
              <AlertTriangle className="h-4 w-4 shrink-0" />{error}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button
              onClick={onClose}
              className="rounded-lg border border-line px-4 py-2 text-sm font-medium text-fg-secondary hover:bg-surface-subtle"
            >
              Cancel
            </button>
            <button
              onClick={onConfirm}
              disabled={loading}
              className="rounded-lg bg-danger-solid px-4 py-2 text-sm font-medium text-white hover:bg-danger-700 disabled:opacity-50"
            >
              {loading ? 'Deleting…' : confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
