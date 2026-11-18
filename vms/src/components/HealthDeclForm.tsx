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
import { AlertCircle, CheckCircle2, Loader2, RotateCw } from 'lucide-react'
import SignatureCanvas from 'react-signature-canvas'
import { Checkbox, Radio, Button, Modal } from '@uniops/shell'
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
    <Modal
      open
      onClose={onClose}
      title={`Health declaration${visitorName ? ` — ${visitorName}` : ''}`}
      description="Required before printing a badge for GMP, Laboratory and Entire Plant visits."
      // Closes by Cancel / the close button only (no Escape, no backdrop), as before.
      closeOnEscape={false}
      size="full"
      className="max-w-2xl"
      bodyClassName="flex min-h-0 flex-col p-0"
    >
        {/* Result banner (only after a submit completes) */}
        {result === 'passed' && (
          <div className="border-b border-success-line bg-success-soft px-5 py-3 text-sm text-success-fg">
            <p className="flex items-center gap-1.5 font-medium">
              <CheckCircle2 className="h-4 w-4" />
              Declaration passed — visitor cleared for the scheduled access area.
            </p>
          </div>
        )}
        {result === 'failed' && (
          <div className="border-b border-danger-line bg-danger-soft px-5 py-3 text-sm text-danger-fg">
            <p className="flex items-center gap-1.5 font-medium">
              <AlertCircle className="h-4 w-4" />
              Declaration failed — visitor cannot enter GMP, Laboratory or Entire Plant areas today.
            </p>
            <p className="mt-1 text-xs">
              To allow office-only access, use “Edit visit” on the visit page to
              change the access zone to a non-GMP zone (e.g. Office), then print the badge.
            </p>
          </div>
        )}

        {/* Body */}
        <div className="overflow-y-auto px-5 py-4">
          {tplLoading && (
            <p className="text-sm text-fg-muted">
              <Loader2 className="inline h-4 w-4 animate-spin" /> Loading questionnaire…
            </p>
          )}

          {!tplLoading && questions.map((q) => (
            <div key={q.id} className="border-b border-line-subtle py-3 last:border-b-0">
              <p className="text-sm text-fg">{q.text}</p>
              <div className="mt-2 inline-flex rounded-md border border-line bg-surface-subtle p-0.5 text-sm">
                {(['yes', 'no'] as const).map(v => (
                  <label
                    key={v}
                    className={
                      'cursor-pointer rounded px-3 py-1 capitalize transition-colors ' +
                      (answers[q.id] === v
                        ? 'bg-surface shadow-sm text-fg font-medium'
                        : 'text-fg-muted hover:text-fg-secondary')
                    }
                  >
                    <Radio
                     
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
            <label className="mt-4 flex items-start gap-2 rounded-md border border-warning-line bg-warning-soft px-3 py-2 text-sm">
              <Checkbox
               
                checked={trainingOK}
                onChange={(e) => setTrainingOK(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium text-warning-fg">Food-safety briefing confirmed</span>
                <span className="block text-xs text-warning-fg">
                  Visitor has been briefed on gowning, hand-wash, and contamination rules
                  for the food production area (PRD VMS-CI-012).
                </span>
              </span>
            </label>
          )}

          {/* Signature */}
          {!tplLoading && (
            <div className="mt-4">
              <p className="text-xs font-semibold text-fg-muted">
                Visitor signature
              </p>
              <div className="mt-1 rounded-md border border-dashed border-line-strong bg-surface-subtle">
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
              <Button variant="subtle" size="xs"
                type="button"
                onClick={() => sigRef.current?.clear()}
                className="mt-1.5"
              >
                <RotateCw className="h-3 w-3" />
                Clear signature
              </Button>
            </div>
          )}

          {/* Error */}
          {submit.error && !result && (
            <p className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-xs text-danger-fg">
              {submit.error.message}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 border-t border-line-subtle px-5 py-3">
          {result ? (
            <Button size="sm"
              onClick={onClose}
             
            >
              Done
            </Button>
          ) : (
            <>
              <Button variant="secondary" size="sm"
                onClick={onClose}
                disabled={submit.isPending}
               
              >
                Cancel
              </Button>
              <Button size="sm"
                onClick={submitForm}
                disabled={!ready || submit.isPending}
               
              >
                {submit.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <CheckCircle2 className="h-4 w-4" />}
                Submit declaration
              </Button>
            </>
          )}
        </div>
    </Modal>
  )
}
