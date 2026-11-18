import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, X } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ConfirmOptions {
  title: string
  /** The question the browser's confirm() used to ask, word for word. */
  message: ReactNode
  confirmLabel: string
  /** danger for destructive actions, primary otherwise. */
  tone?: 'danger' | 'primary'
}

export interface NoticeOptions {
  title: string
  message: ReactNode
}

type Pending =
  | { kind: 'confirm'; options: ConfirmOptions; resolve: (ok: boolean) => void }
  | { kind: 'notice'; options: NoticeOptions; resolve: () => void }

/**
 * In-page replacement for the browser's confirm() and alert() in the Portal
 * admin pages, used in the flow exactly where they were:
 *   const dlg = useConfirmDialog()
 *   if (!(await dlg.confirm({ title, message, confirmLabel }))) return
 *   await dlg.notify({ title, message })        // was alert(); waits for OK
 *   …and render {dlg.dialog} once in the component.
 * Escape, Cancel and the close button answer "no". The caller's own busy
 * state keeps its button disabled while the confirmed request runs.
 */
export function useConfirmDialog() {
  const [pending, setPending] = useState<Pending | null>(null)

  const confirm = useCallback((options: ConfirmOptions) => new Promise<boolean>((resolve) => {
    setPending({ kind: 'confirm', options, resolve })
  }), [])
  const notify = useCallback((options: NoticeOptions) => new Promise<void>((resolve) => {
    setPending({ kind: 'notice', options, resolve })
  }), [])

  const answer = useCallback((ok: boolean) => {
    setPending((p) => {
      if (p?.kind === 'confirm') p.resolve(ok)
      else p?.resolve()
      return null
    })
  }, [])

  return {
    confirm,
    notify,
    dialog: pending ? <DialogBox pending={pending} onAnswer={answer} /> : null,
  }
}

function DialogBox({ pending, onAnswer }: { pending: Pending; onAnswer: (ok: boolean) => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onAnswer(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onAnswer])

  const isConfirm = pending.kind === 'confirm'
  const danger = isConfirm && (pending.options.tone ?? 'danger') === 'danger'
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-surface shadow-xl" role={isConfirm ? 'dialog' : 'alertdialog'} aria-modal="true" aria-label={pending.options.title}>
        <div className="flex items-center justify-between border-b border-line-subtle px-5 py-4">
          <div className="flex items-center gap-2">
            <AlertTriangle className={cn('h-5 w-5', danger ? 'text-danger-fg' : 'text-warning-fg')} />
            <h3 className="text-sm font-semibold text-fg">{pending.options.title}</h3>
          </div>
          <button onClick={() => onAnswer(false)} aria-label="Close" className="rounded p-1 text-fg-muted hover:bg-surface-sunken">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex flex-col gap-4 p-5">
          <div className="whitespace-pre-line text-sm text-fg-secondary">{pending.options.message}</div>
          <div className="flex justify-end gap-2">
            {isConfirm && (
              <button onClick={() => onAnswer(false)} className="rounded-lg border border-line px-4 py-2 text-sm text-fg-secondary hover:bg-surface-subtle">
                Cancel
              </button>
            )}
            <button
              onClick={() => onAnswer(true)}
              autoFocus
              className={cn(
                'rounded-lg px-4 py-2 text-sm font-medium text-white',
                danger ? 'bg-danger-solid hover:bg-danger-700' : 'bg-primary-700 hover:bg-primary-800',
              )}
            >
              {isConfirm ? pending.options.confirmLabel : 'OK'}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
