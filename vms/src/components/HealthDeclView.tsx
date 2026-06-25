/** Read-only view of a signed health declaration — questions + answers + the
 *  visitor's e-signature. Shared by the visit detail page and the standalone
 *  declarations browser. Pure presentational: caller supplies the data.
 */
import { CheckCircle2, AlertCircle, X } from 'lucide-react'
import type { HealthDeclarationAnswer, HealthDeclStatus } from '@/services/api'
import { formatDateTime } from '@/lib/utils'

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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
        {/* Header */}
        <div className="flex items-start justify-between border-b border-neutral-100 px-5 py-4">
          <div>
            <h3 className="text-base font-semibold text-neutral-900">
              Health declaration — {visitorName}
            </h3>
            <p className="mt-0.5 text-xs text-neutral-500">Filed {formatDateTime(submittedAt)}</p>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-neutral-400 hover:bg-neutral-50 hover:text-neutral-600"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Result banner */}
        <div
          className={
            'flex items-center gap-1.5 border-b px-5 py-2.5 text-sm font-medium ' +
            (passed
              ? 'border-emerald-200 bg-success-50 text-success-600'
              : 'border-red-200 bg-danger-50 text-danger-600')
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
                className="flex items-start justify-between gap-3 border-b border-neutral-100 py-2 last:border-b-0"
              >
                <dt className="text-sm text-neutral-700">{a.text || a.id}</dt>
                <dd className="shrink-0 text-sm font-medium capitalize text-neutral-900">{a.answer}</dd>
              </div>
            ))}
          </dl>

          <div className="mt-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
              Visitor signature
            </p>
            {signature ? (
              <img
                src={signature}
                alt="Visitor signature"
                className="mt-1 max-h-40 rounded-md border border-neutral-200 bg-white"
              />
            ) : (
              <p className="mt-1 text-xs text-neutral-400">No signature captured.</p>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end border-t border-neutral-100 px-5 py-3">
          <button
            onClick={onClose}
            className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
