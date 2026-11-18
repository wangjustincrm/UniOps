/** Read-only view of a signed health declaration — questions + answers + the
 *  visitor's e-signature. Shared by the visit detail page and the standalone
 *  declarations browser. Pure presentational: caller supplies the data.
 */
import { CheckCircle2, AlertCircle } from 'lucide-react'
import type { HealthDeclarationAnswer, HealthDeclStatus } from '@/services/api'
import { formatDateTime } from '@/lib/utils'

import { Button, Modal } from '@uniops/shell'
interface Props {
  visitorName: string
  result: HealthDeclStatus
  answers: HealthDeclarationAnswer[]
  signature: string | null
  submittedAt: string
  onClose: () => void
}

export function HealthDeclView({
  visitorName, result, answers, signature, submittedAt, onClose,
}: Props) {
  const passed = result === 'passed'
  return (
    <Modal
      open
      onClose={onClose}
      title={`Health declaration — ${visitorName}`}
      description={`Filed ${formatDateTime(submittedAt)}`}
      // Closes by Cancel / the close button only (no Escape, no backdrop), as before.
      closeOnEscape={false}
      size="full"
      className="max-w-2xl"
      bodyClassName="flex min-h-0 flex-col p-0"
    >
        {/* Result banner */}
        <div
          className={
            'flex items-center gap-1.5 border-b px-5 py-2.5 text-sm font-medium ' +
            (passed
              ? 'border-success-line bg-success-soft text-success-fg'
              : 'border-danger-line bg-danger-soft text-danger-fg')
          }
        >
          {passed ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
          {passed ? 'Passed' : result === 'failed' ? 'Failed' : result}
        </div>

        {/* Body */}
        <div className="overflow-y-auto px-5 py-4">
          <dl className="space-y-0">
            {answers.map((a) => (
              <div
                key={a.id}
                className="flex items-start justify-between gap-3 border-b border-line-subtle py-2 last:border-b-0"
              >
                <dt className="text-sm text-fg-secondary">{a.text || a.id}</dt>
                <dd className="shrink-0 text-sm font-medium capitalize text-fg">{a.answer}</dd>
              </div>
            ))}
          </dl>

          <div className="mt-4">
            <p className="text-xs font-semibold text-fg-muted">
              Visitor signature
            </p>
            {signature ? (
              <img
                src={signature}
                alt="Visitor signature"
                className="mt-1 max-h-40 rounded-md border border-line bg-surface"
              />
            ) : (
              <p className="mt-1 text-xs text-fg-muted">No signature captured.</p>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end border-t border-line-subtle px-5 py-3">
          <Button size="sm"
            onClick={onClose}
           
          >
            Close
          </Button>
        </div>
    </Modal>
  )
}
