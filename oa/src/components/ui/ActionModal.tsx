import { useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { CheckCircle2, XCircle, RotateCcw, Send, X, MessageSquare, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ACTION } from '@/lib/status'

type Cfg = { title: string; label: string; icon: ReactNode; bg: string; btn: string }

const CFG: Record<string, Cfg> = {
  [ACTION.SUBMIT]:  { title: 'Submit for Approval', label: 'Submit',  icon: <Send className="h-5 w-5 text-primary-600" />,       bg: 'bg-primary-50', btn: 'bg-primary-700 hover:bg-primary-800 text-white' },
  [ACTION.APPROVE]: { title: 'Approve',             label: 'Approve', icon: <CheckCircle2 className="h-5 w-5 text-success-600" />, bg: 'bg-success-50', btn: 'bg-success-600 hover:bg-success-700 text-white' },
  [ACTION.RETURN]:  { title: 'Return for Revision', label: 'Return',  icon: <RotateCcw className="h-5 w-5 text-warning-600" />,    bg: 'bg-warning-50', btn: 'bg-warning-500 hover:bg-warning-600 text-white' },
  [ACTION.REJECT]:  { title: 'Reject',              label: 'Reject',  icon: <XCircle className="h-5 w-5 text-danger-600" />,       bg: 'bg-danger-50',  btn: 'bg-danger-600 hover:bg-danger-700 text-white' },
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
  const cfg = CFG[action] ?? { title: action, label: action, icon: null, bg: 'bg-neutral-100', btn: 'bg-neutral-800 text-white' }
  const needsComment = action === ACTION.RETURN || action === ACTION.REJECT

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className={cn('flex h-9 w-9 items-center justify-center rounded-full', cfg.bg)}>{cfg.icon}</div>
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">{cfg.title}</h2>
              {docNumber && <p className="text-xs text-neutral-500">{docNumber}</p>}
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100"><X className="h-4 w-4" /></button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">{needsComment ? 'Reason *' : 'Comment (optional)'}</label>
            <div className="relative">
              <MessageSquare className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-neutral-400" />
              <textarea
                rows={3} value={comment} onChange={(e) => setComment(e.target.value)} autoFocus
                placeholder="Add a comment…"
                className="w-full resize-none rounded-lg border border-neutral-300 bg-white pl-8 pr-3 pt-2 pb-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
              />
            </div>
          </div>
          {error && (
            <div className="flex items-center gap-2 rounded-lg bg-danger-50 border border-danger-200 px-3 py-2 text-sm text-danger-700">
              <AlertTriangle className="h-4 w-4 shrink-0" />{error}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-neutral-200 px-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50">Cancel</button>
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
