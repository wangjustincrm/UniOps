import { useCallback, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, CheckCircle2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useCloseOnEscape } from '@/hooks/useCloseOnEscape'
import { runConfirmAction } from '@/lib/confirmAction'
import { cn } from '@/lib/utils'

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
 * In-page confirmation, in the same shape as the PA / agreement Reject
 * dialogs, used wherever the pages used the browser's native confirm().
 * Escape, Cancel and the close button close it without doing anything.
 */
export function ConfirmDialog({
  title, subtitle, message, confirmLabel, cancelLabel = 'Cancel', tone = 'danger', onConfirm, failureFallback, onClose,
}: ConfirmOptions & { onClose: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const close = useCallback(() => { if (!busy) onClose() }, [busy, onClose])
  useCloseOnEscape(true, close)

  const confirm = async () => {
    setBusy(true)
    setError(null)
    const failure = await runConfirmAction(onConfirm, failureFallback)
    setBusy(false)
    if (failure === null) onClose()
    else setError(failure)
  }

  const danger = tone === 'danger'
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl" role="dialog" aria-modal="true" aria-label={title}>
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className={cn('flex h-9 w-9 items-center justify-center rounded-full', danger ? 'bg-danger-50' : 'bg-warning-50')}>
              <AlertTriangle className={cn('h-5 w-5', danger ? 'text-danger-600' : 'text-warning-600')} />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">{title}</h2>
              {subtitle && <p className="text-xs text-neutral-500">{subtitle}</p>}
            </div>
          </div>
          <button onClick={close} disabled={busy} aria-label="Close" className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-600 disabled:opacity-40">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <div className="text-sm text-neutral-700 whitespace-pre-line">{message}</div>
          {error && (
            <p role="alert" className="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700 whitespace-pre-line">{error}</p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={close} disabled={busy}>{cancelLabel}</Button>
            <button
              onClick={confirm}
              disabled={busy}
              autoFocus
              className={cn(
                'inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
                danger ? 'bg-danger-600 hover:bg-danger-700' : 'bg-primary-600 hover:bg-primary-700',
              )}
            >
              {busy ? 'Working…' : confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
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
  useCloseOnEscape(true, onClose)
  const error = tone === 'error'
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl" role="alertdialog" aria-modal="true" aria-label={title}>
        <div className="flex items-center gap-3 border-b border-neutral-100 px-6 py-4">
          <div className={cn('flex h-9 w-9 items-center justify-center rounded-full', error ? 'bg-danger-50' : 'bg-warning-50')}>
            <AlertTriangle className={cn('h-5 w-5', error ? 'text-danger-600' : 'text-warning-600')} />
          </div>
          <h2 className="text-sm font-semibold text-neutral-900">{title}</h2>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <div className="text-sm text-neutral-700 whitespace-pre-line">{message}</div>
          <div className="flex justify-end">
            <Button size="sm" onClick={onClose} autoFocus>OK</Button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
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
  const ok = tone === 'success'
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={cn(
        'flex items-start gap-2 rounded-lg border px-3 py-2 text-sm whitespace-pre-line',
        tone === 'error' && 'border-danger-200 bg-danger-50 text-danger-700',
        tone === 'warning' && 'border-warning-200 bg-warning-50 text-warning-700',
        ok && 'border-success-200 bg-success-50 text-success-700',
        className,
      )}
    >
      {ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}
      <div className="flex-1">{children}</div>
      {onDismiss && (
        <button onClick={onDismiss} aria-label="Dismiss" className="rounded p-0.5 opacity-70 hover:opacity-100">
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  )
}
