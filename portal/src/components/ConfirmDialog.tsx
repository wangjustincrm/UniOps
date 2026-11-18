import { useCallback, useState, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'

import { Button, Modal } from '@uniops/shell'
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
  const isConfirm = pending.kind === 'confirm'
  const danger = isConfirm && (pending.options.tone ?? 'danger') === 'danger'
  // Escape, Cancel and the close button answer "no"; no backdrop close (as
  // before). `stacked`: these often ask from inside another dialog.
  return (
    <Modal
      open
      stacked
      onClose={() => onAnswer(false)}
      title={pending.options.title}
      icon={<AlertTriangle className={cn('h-5 w-5', danger ? 'text-danger-fg' : 'text-warning-fg')} />}
      size="full"
      className="max-w-md"
      footer={<>
        {isConfirm && (
          <Button variant="secondary" size="md" onClick={() => onAnswer(false)}>
            Cancel
          </Button>
        )}
        <Button variant={danger ? 'danger' : 'primary'} size="md" data-autofocus onClick={() => onAnswer(true)}>
          {isConfirm ? pending.options.confirmLabel : 'OK'}
        </Button>
      </>}
    >
      <div className="whitespace-pre-line text-sm text-fg-secondary">{pending.options.message}</div>
    </Modal>
  )
}
