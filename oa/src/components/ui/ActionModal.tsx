import { useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { CheckCircle2, XCircle, RotateCcw, Send, X, MessageSquare, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ACTION } from '@/lib/status'

type Cfg = { title: string; label: string; icon: ReactNode; bg: string; btn: string }

const CFG: Record<string, Cfg> = {
  [ACTION.SUBMIT]:  { title: 'Submit for Approval', label: 'Submit',  icon: <Send className="h-5 w-5 text-primary-600" />,       bg: 'bg-primary-50', btn: 'bg-primary-700 hover:bg-primary-800 text-white' },
  [ACTION.APPROVE]: { title: 'Approve',             label: 'Approve', icon: <CheckCircle2 className="h-5 w-5 text-success-fg" />, bg: 'bg-success-soft', btn: 'bg-success-solid hover:bg-success-700 text-white' },
  [ACTION.RETURN]:  { title: 'Return for Revision', label: 'Return',  icon: <RotateCcw className="h-5 w-5 text-warning-fg" />,    bg: 'bg-warning-soft', btn: 'bg-warning-solid hover:bg-warning-solid text-white' },
  [ACTION.REJECT]:  { title: 'Reject',              label: 'Reject',  icon: <XCircle className="h-5 w-5 text-danger-fg" />,       bg: 'bg-danger-soft',  btn: 'bg-danger-solid hover:bg-danger-700 text-white' },
}

export function ActionModal({
  action, docNumber, onConfirm, onClose, loading, error,
}: {
  action: string
  docNumber?: string
  onConfirm: (comment: string) => void
  onClose: () => void
  loading: boolean
  error?: string
}) {
  const [comment, setComment] = useState('')
  const cfg = CFG[action] ?? { title: action, label: action, icon: null, bg: 'bg-surface-sunken', btn: 'bg-inverse text-white' }
  const needsComment = action === ACTION.RETURN || action === ACTION.REJECT

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-inverse/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-line-subtle px-6 py-4">
          <div className="flex items-center gap-3">
            <div className={cn('flex h-9 w-9 items-center justify-center rounded-full', cfg.bg)}>{cfg.icon}</div>
            <div>
              <h2 className="text-sm font-semibold text-fg">{cfg.title}</h2>
              {docNumber && <p className="text-xs text-fg-muted">{docNumber}</p>}
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-fg-muted hover:bg-surface-sunken"><X className="h-4 w-4" /></button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">{needsComment ? 'Reason *' : 'Comment (optional)'}</label>
            <div className="relative">
              <MessageSquare className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-fg-muted" />
              <textarea
                rows={3} value={comment} onChange={(e) => setComment(e.target.value)} autoFocus
                placeholder="Add a comment…"
                className="w-full resize-none rounded-lg border border-line-strong bg-surface pl-8 pr-3 pt-2 pb-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
              />
            </div>
          </div>
          {error && (
            <div className="flex items-center gap-2 rounded-lg bg-danger-soft border border-danger-line px-3 py-2 text-sm text-danger-fg">
              <AlertTriangle className="h-4 w-4 shrink-0" />{error}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-3 text-sm font-medium text-fg-secondary hover:bg-surface-subtle">Cancel</button>
            <button type="button" disabled={loading || (needsComment && !comment.trim())} onClick={() => onConfirm(comment)}
              className={cn('inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed', cfg.btn)}>
              {cfg.icon}{loading ? 'Processing…' : cfg.label}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
