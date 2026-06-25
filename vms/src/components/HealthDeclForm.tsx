/** Health declaration form (PRD §2.2.2 VMS-CI-010..013).
 *
 * Renders the active questionnaire (fetched from `/health-questions`), captures
 * yes/no answers + safety-training confirmation + e-signature, posts to
 * `/visits/{id}/health-declaration`. Server computes pass/fail — we just
 * surface the result.
 *
 * Designed as a modal-style overlay (caller controls open/close).
 */
import { useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Loader2, RotateCw, X } from 'lucide-react'
import SignatureCanvas from 'react-signature-canvas'
import {
  useHealthQuestions, useSubmitHealthDeclaration,
} from '@/services/api'

interface Props {
  visitId: string
  /** Which visitor on the visit is filing this declaration. */
  visitorId: string
  /** Visitor name, shown in the form header so the operator knows who signs. */
  visitorName?: string
  onClose: () => void
  onSubmitted?: (result: 'passed' | 'failed') => void
}

export function HealthDeclForm({ visitId, visitorId, visitorName, onClose, onSubmitted }: Props) {
  const { data: template, isLoading: tplLoading } = useHealthQuestions()
  const submit = useSubmitHealthDeclaration(visitId)

  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [trainingOK, setTrainingOK] = useState(false)
  const sigRef = useRef<SignatureCanvas | null>(null)

  const questions = template?.questions ?? []

  const allAnswered = questions.every(q => answers[q.id])
  const ready = allAnswered && trainingOK && !tplLoading

  const submitForm = () => {
    const signature = sigRef.current && !sigRef.current.isEmpty()
      ? sigRef.current.toDataURL('image/png')
      : null

    submit.mutate(
      {
        answers: questions.map(q => ({ id: q.id, answer: answers[q.id] })),
        safety_training_confirmed: trainingOK,
        signature,
        visitor_id: visitorId,
      },
      {
        onSuccess: (decl) => {
          const r = decl.result === 'passed' ? 'passed' : 'failed'
          onSubmitted?.(r)
        },
      },
    )
  }

  // If we got a submission result, show it inline before closing.
  const result = submit.data?.result

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
        {/* Header */}
        <div className="flex items-start justify-between border-b border-neutral-100 px-5 py-4">
          <div>
            <h3 className="text-base font-semibold text-neutral-900">
              Health declaration{visitorName ? ` — ${visitorName}` : ''}
            </h3>
            <p className="mt-0.5 text-xs text-neutral-500">
              Required before printing a badge for GMP / Laboratory zones.
              Ask the visitor each question; mark “Yes” / “No” based on their answer.
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-neutral-400 hover:bg-neutral-50 hover:text-neutral-600"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Result banner (only after a submit completes) */}
        {result === 'passed' && (
          <div className="border-b border-emerald-200 bg-success-50 px-5 py-3 text-sm text-success-600">
            <p className="flex items-center gap-1.5 font-medium">
              <CheckCircle2 className="h-4 w-4" />
              Declaration passed — visitor cleared for the scheduled access area.
            </p>
          </div>
        )}
        {result === 'failed' && (
          <div className="border-b border-red-200 bg-danger-50 px-5 py-3 text-sm text-danger-600">
            <p className="flex items-center gap-1.5 font-medium">
              <AlertCircle className="h-4 w-4" />
              Declaration failed — visitor cannot enter GMP / Lab areas today.
            </p>
            <p className="mt-1 text-xs">
              To allow office-only access, change the access area on the visit
              and reprint at the downgraded badge.
            </p>
          </div>
        )}

        {/* Body */}
        <div className="overflow-y-auto px-5 py-4">
          {tplLoading && (
            <p className="text-sm text-neutral-400">
              <Loader2 className="inline h-4 w-4 animate-spin" /> Loading questionnaire…
            </p>
          )}

          {!tplLoading && questions.map((q) => (
            <div key={q.id} className="border-b border-neutral-100 py-3 last:border-b-0">
              <p className="text-sm text-neutral-800">{q.text}</p>
              <div className="mt-2 inline-flex rounded-md border border-neutral-200 bg-neutral-50 p-0.5 text-sm">
                {(['yes', 'no'] as const).map(v => (
                  <label
                    key={v}
                    className={
                      'cursor-pointer rounded px-3 py-1 capitalize transition-colors ' +
                      (answers[q.id] === v
                        ? 'bg-white shadow-sm text-neutral-900 font-medium'
                        : 'text-neutral-500 hover:text-neutral-700')
                    }
                  >
                    <input
                      type="radio"
                      name={q.id}
                      value={v}
                      checked={answers[q.id] === v}
                      onChange={() => setAnswers(prev => ({ ...prev, [q.id]: v }))}
                      className="sr-only"
                    />
                    {v}
                  </label>
                ))}
              </div>
            </div>
          ))}

          {/* Safety training confirmation */}
          {!tplLoading && (
            <label className="mt-4 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm">
              <input
                type="checkbox"
                checked={trainingOK}
                onChange={(e) => setTrainingOK(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium text-amber-800">Food-safety briefing confirmed</span>
                <span className="block text-xs text-amber-700">
                  Visitor has been briefed on gowning, hand-wash, and contamination rules
                  for the food production area (PRD VMS-CI-012).
                </span>
              </span>
            </label>
          )}

          {/* Signature */}
          {!tplLoading && (
            <div className="mt-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
                Visitor signature
              </p>
              <div className="mt-1 rounded-md border border-dashed border-neutral-300 bg-neutral-50">
                <SignatureCanvas
                  ref={sigRef}
                  canvasProps={{
                    width: 600,
                    height: 140,
                    className: 'w-full',
                  }}
                  penColor="#1A2730"
                />
              </div>
              <button
                type="button"
                onClick={() => sigRef.current?.clear()}
                className="mt-1.5 inline-flex items-center gap-1 text-xs text-neutral-500 hover:text-neutral-700"
              >
                <RotateCw className="h-3 w-3" />
                Clear signature
              </button>
            </div>
          )}

          {/* Error */}
          {submit.error && !result && (
            <p className="mt-3 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">
              {submit.error.message}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 border-t border-neutral-100 px-5 py-3">
          {result ? (
            <button
              onClick={onClose}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700"
            >
              Done
            </button>
          ) : (
            <>
              <button
                onClick={onClose}
                disabled={submit.isPending}
                className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                onClick={submitForm}
                disabled={!ready || submit.isPending}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
              >
                {submit.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <CheckCircle2 className="h-4 w-4" />}
                Submit declaration
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
