import { useState, type ReactNode } from 'react'
import { CheckCircle2, XCircle, RotateCcw, Send, MessageSquare } from 'lucide-react'
import { ACTION } from '@/lib/status'

import { Alert, Button, Modal, Textarea, type ButtonProps } from '@uniops/shell'
type Cfg = { title: string; label: string; icon: ReactNode; variant: ButtonProps['variant'] }

const CFG: Record<string, Cfg> = {
  [ACTION.SUBMIT]:  { title: 'Submit for Approval', label: 'Submit',  icon: <Send className="h-4 w-4" />,         variant: 'primary' },
  [ACTION.APPROVE]: { title: 'Approve',             label: 'Approve', icon: <CheckCircle2 className="h-4 w-4" />, variant: 'success' },
  [ACTION.RETURN]:  { title: 'Return for Revision', label: 'Return',  icon: <RotateCcw className="h-4 w-4" />,    variant: 'warning' },
  [ACTION.REJECT]:  { title: 'Reject',              label: 'Reject',  icon: <XCircle className="h-4 w-4" />,      variant: 'danger' },
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
  const cfg = CFG[action] ?? { title: action, label: action, icon: null, variant: 'primary' as const }
  const needsComment = action === ACTION.RETURN || action === ACTION.REJECT

  // Closes only by Cancel / the close button, as before (no Escape, no backdrop).
  return (
    <Modal
      open
      onClose={onClose}
      closeOnEscape={false}
      title={cfg.title}
      description={docNumber}
      icon={cfg.icon}
      footer={<>
        <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
        <Button variant={cfg.variant} size="sm" disabled={loading || (needsComment && !comment.trim())} onClick={() => onConfirm(comment)}>
          {cfg.icon}{loading ? 'Processing…' : cfg.label}
        </Button>
      </>}
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">{needsComment ? 'Reason *' : 'Comment (optional)'}</label>
          <div className="relative">
            <MessageSquare className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-fg-muted" />
            <Textarea
              rows={3} value={comment} onChange={(e) => setComment(e.target.value)} autoFocus
              placeholder="Add a comment…"
              className="w-full resize-none pl-8 pr-3 pt-2 pb-2"
            />
          </div>
        </div>
        {error && <Alert tone="danger">{error}</Alert>}
      </div>
    </Modal>
  )
}
