import { useCallback, useState, type ReactNode } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { Alert, ConfirmDialog as ShellConfirmDialog, IconButton, Modal } from '@uniops/shell'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/submitFailure'

/**
 * EPMS's in-page confirm / notice / message, drawn by the Shell (Modal,
 * ConfirmDialog, Alert). The API is unchanged so the ~20 callers keep working.
 */

export interface ConfirmOptions {
  /** Short heading, e.g. "Cancel GR". */
  title: string
  /** The document it acts on, e.g. its number. */
  subtitle?: string
  /** The question the browser's confirm() used to ask, word for word. */
  message: ReactNode
  confirmLabel: string
  /** The dismiss button; "Cancel" unless the action itself is a cancel. */
  cancelLabel?: string
  /** danger for destructive actions (delete / cancel / void), primary otherwise. */
  tone?: 'danger' | 'primary'
  /** What the confirm button does. While a returned promise is pending the
   *  button is disabled (UI-BTN-001); if it throws, the message is shown in the
   *  dialog and the dialog stays open; otherwise the dialog closes. */
  onConfirm: () => unknown
  /** Shown when the action throws something without a message. */
  failureFallback?: string
}

/**
 * In-page confirmation, used wherever the pages used the browser's native
 * confirm(). Escape, Cancel and the close button close it without doing anything.
 */
export function ConfirmDialog({
  title, subtitle, message, confirmLabel, cancelLabel = 'Cancel', tone = 'danger', onConfirm, failureFallback, onClose,
}: ConfirmOptions & { onClose: () => void }) {
  return (
    <ShellConfirmDialog
      title={title}
      subtitle={subtitle}
      message={message}
      confirmLabel={confirmLabel}
      cancelLabel={cancelLabel}
      tone={tone}
      onConfirm={onConfirm}
      onClose={onClose}
      // Same wording rules as runConfirmAction (lib/confirmAction).
      failureFallback={failureFallback ?? 'The action failed'}
      formatError={(err, fallback) => errorMessage(err, fallback)}
    />
  )
}

/**
 * Drop-in for `if (confirm(...)) doIt()`:
 *   const confirmDlg = useConfirmDialog()
 *   confirmDlg.ask({ title, message, confirmLabel, onConfirm: doIt })
 *   …and render {confirmDlg.dialog} once in the component.
 */
export function useConfirmDialog() {
  const [options, setOptions] = useState<ConfirmOptions | null>(null)
  const close = useCallback(() => setOptions(null), [])
  const ask = useCallback((o: ConfirmOptions) => setOptions(o), [])
  return {
    ask,
    dialog: options ? <ConfirmDialog {...options} onClose={close} /> : null,
  }
}

export interface NoticeOptions {
  title: string
  message: ReactNode
  /** warning: something went only half right; error: it failed. */
  tone?: 'warning' | 'error'
}

/**
 * An in-page message with a single OK, for the few places that used alert()
 * to report a half-done result just before the page moves on (e.g. "saved,
 * but the attachment failed to upload"): the flow waits for OK exactly as
 * it waited for the browser's alert.
 */
export function NoticeDialog({ title, message, tone = 'warning', onClose }: NoticeOptions & { onClose: () => void }) {
  return (
    <Modal
      open
      onClose={onClose}
      size="sm"
      title={title}
      icon={<AlertTriangle className={tone === 'error' ? 'text-danger-fg' : 'text-warning-fg'} />}
      footer={<Button size="sm" onClick={onClose} data-autofocus>OK</Button>}
    >
      <div className="whitespace-pre-line text-sm text-fg-secondary">{message}</div>
    </Modal>
  )
}

/**
 * Drop-in for `alert(msg)` in an async flow:
 *   const notice = useNoticeDialog()
 *   await notice.show({ title, message })   // resolves on OK / Escape
 *   …and render {notice.dialog} once in the component.
 */
export function useNoticeDialog() {
  const [pending, setPending] = useState<{ options: NoticeOptions; resolve: () => void } | null>(null)
  const show = useCallback((options: NoticeOptions) => new Promise<void>((resolve) => {
    setPending({ options, resolve })
  }), [])
  const close = useCallback(() => {
    setPending((p) => { p?.resolve(); return null })
  }, [])
  return {
    show,
    dialog: pending ? <NoticeDialog {...pending.options} onClose={close} /> : null,
  }
}

/**
 * A result or refusal shown on the page next to the action that caused it,
 * where the pages used to call the browser's alert().
 */
export function ActionMessage({ tone = 'error', children, onDismiss, className }: {
  /** error: it failed; warning: done in part; success: done. */
  tone?: 'error' | 'warning' | 'success'
  children: ReactNode
  onDismiss?: () => void
  className?: string
}) {
  return (
    <Alert
      tone={tone === 'error' ? 'danger' : tone}
      className={className}
      action={onDismiss && (
        <IconButton label="Dismiss" icon={<X className="h-3.5 w-3.5" />} size="icon-xs" variant="subtle"
          className="-my-1 text-current opacity-70 hover:opacity-100" onClick={onDismiss} />
      )}
    >
      {children}
    </Alert>
  )
}

