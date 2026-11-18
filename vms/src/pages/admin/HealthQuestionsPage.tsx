/** Health questionnaire template editor (W11).
 *
 * The template drives the HealthDeclForm questionnaire. Question ids are
 * stable identifiers used in audit-log answer keys — renaming an id
 * orphans historical answers, so we surface a warning when the user
 * removes or renames an id that was in the previous template.
 *
 * React 19 strict: uses "draft override" pattern instead of
 * useEffect-driven setState; `crypto.randomUUID()` is called inside event
 * handlers (not during render) for new question ids.
 */
import { useMemo, useState } from 'react'
import { Input, Select, Button } from '@uniops/shell'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Loader2, Plus, RotateCcw, X } from 'lucide-react'
import {
  useHealthQuestionsAdmin, useSetHealthQuestions,
  type HealthQuestionEntry, type HealthQuestionsTemplateFull,
} from '@/services/api'

const EMPTY: HealthQuestionsTemplateFull = { version: 1, questions: [] }

export default function HealthQuestionsPage() {
  const { data, isLoading } = useHealthQuestionsAdmin()
  const save = useSetHealthQuestions()

  // Draft override: null until first edit. After Save it resets to null.
  const [draft, setDraft] = useState<HealthQuestionsTemplateFull | null>(null)
  const current = draft ?? data ?? EMPTY

  const originalIds = useMemo(
    () => new Set((data?.questions ?? []).map(q => q.id)),
    [data],
  )

  const setVersion = (v: number) =>
    setDraft({ ...current, version: v })

  const setQuestions = (next: HealthQuestionEntry[]) =>
    setDraft({ ...current, questions: next })

  const addQuestion = () => {
    // crypto.randomUUID is browser-safe; called from event handler, not render.
    const id = `q_${crypto.randomUUID().slice(0, 5)}`
    setQuestions([...current.questions, { id, text: '', fail_on: 'yes' }])
  }

  const updateQuestion = (idx: number, patch: Partial<HealthQuestionEntry>) => {
    const next = [...current.questions]
    next[idx] = { ...next[idx], ...patch }
    setQuestions(next)
  }

  const removeQuestion = (idx: number) =>
    setQuestions(current.questions.filter((_, i) => i !== idx))

  const move = (idx: number, dir: -1 | 1) => {
    const j = idx + dir
    if (j < 0 || j >= current.questions.length) return
    const next = [...current.questions]
    ;[next[idx], next[j]] = [next[j], next[idx]]
    setQuestions(next)
  }

  // Validation
  const ids = current.questions.map(q => q.id)
  const dupSet = new Set(ids.filter((id, i, arr) => arr.indexOf(id) !== i))
  const hasDups = dupSet.size > 0
  const hasEmpty = current.questions.some(q => !q.id.trim() || !q.text.trim())
  const removedIds = [...originalIds].filter(id => !ids.includes(id))
  const dirty = draft !== null
  const canSave = dirty && !hasDups && !hasEmpty

  const onSave = () => {
    const payload: HealthQuestionsTemplateFull = {
      version: current.version,
      questions: current.questions.map(q => ({
        id: q.id.trim(),
        text: q.text.trim(),
        fail_on: q.fail_on.trim().toLowerCase() || 'yes',
      })),
    }
    save.mutate(payload, { onSuccess: () => setDraft(null) })
  }

  return (
    <div className="max-w-3xl">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold text-fg">Health questionnaire</h2>
          <p className="mt-0.5 text-xs text-fg-muted">
            Edit the yes/no questions that drive the health declaration form for
            GMP / Lab visits. Question text is snapshotted into each declaration
            at submit time — past declarations stay readable after edits.
          </p>
        </div>
        <Button size="sm"
          onClick={onSave}
          disabled={!canSave || save.isPending}
         
        >
          {save.isPending
            ? <Loader2 className="h-4 w-4 animate-spin" />
            : <CheckCircle2 className="h-4 w-4" />}
          Save
        </Button>
      </div>

      {/* Version + bump */}
      <div className="mt-4 flex items-center gap-2 text-sm">
        <label className="text-fg-secondary">Template version:</label>
        <Input inputSize="sm"
          type="number"
          min="1"
          value={current.version}
          onChange={(e) => setVersion(parseInt(e.target.value) || 1)}
          className="w-20"
        />
        <Button variant="subtle" size="xs"
          type="button"
          onClick={() => setVersion(current.version + 1)}
         
        >
          <RotateCcw className="h-3 w-3" />
          Bump
        </Button>
      </div>

      {/* Warnings */}
      {removedIds.length > 0 && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-warning-line bg-warning-soft px-3 py-2 text-xs text-warning-fg">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <div>
            <p className="font-medium">Removed / renamed question ids:</p>
            <p className="font-mono">{removedIds.join(', ')}</p>
            <p className="mt-1">
              Historical declarations referencing these ids stay readable
              (text is snapshotted on submit) but new declarations won't
              contain them.
            </p>
          </div>
        </div>
      )}
      {hasDups && (
        <p className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-xs text-danger-fg">
          Duplicate question ids: {[...dupSet].join(', ')}
        </p>
      )}
      {save.error && (
        <p className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-xs text-danger-fg">
          {save.error.message}
        </p>
      )}
      {save.isSuccess && !dirty && (
        <p className="mt-3 rounded-md bg-success-soft px-3 py-2 text-xs text-success-fg">
          Template saved. New visits will use this version.
        </p>
      )}

      {/* Questions */}
      <div className="mt-5 space-y-2">
        {isLoading && (
          <p className="text-sm text-fg-muted">Loading…</p>
        )}
        {current.questions.map((q, idx) => (
          <div
            key={idx}
            className={
              'rounded-md border bg-surface p-3 ' +
              (dupSet.has(q.id) ? 'border-danger-line' : 'border-line')
            }
          >
            <div className="grid grid-cols-1 gap-2 md:grid-cols-[180px_1fr_120px_auto]">
              <Input inputSize="sm"
                value={q.id}
                onChange={(e) => updateQuestion(idx, { id: e.target.value })}
                placeholder="id"
                className="font-mono"
              />
              <Input
                value={q.text}
                onChange={(e) => updateQuestion(idx, { text: e.target.value })}
                placeholder="Question text"
               
              />
              <Select
                value={q.fail_on}
                onChange={(e) => updateQuestion(idx, { fail_on: e.target.value })}
               
              >
                <option value="yes">fail on YES</option>
                <option value="no">fail on NO</option>
              </Select>
              <div className="flex items-center gap-1">
                <Button variant="subtle" size="icon-xs" aria-label="Move up"
                  onClick={() => move(idx, -1)}
                  disabled={idx === 0}
                ><ChevronUp className="h-3.5 w-3.5" /></Button>
                <Button variant="subtle" size="icon-xs" aria-label="Move down"
                  onClick={() => move(idx, 1)}
                  disabled={idx === current.questions.length - 1}
                 
                ><ChevronDown className="h-3.5 w-3.5" /></Button>
                <Button variant="subtle" size="icon-xs" aria-label="Remove question"
                  onClick={() => removeQuestion(idx)}
                  className="text-danger-fg hover:bg-danger-soft"
                ><X className="h-3.5 w-3.5" /></Button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <Button variant="secondary" size="md"
        onClick={addQuestion}
        className="mt-3"
      >
        <Plus className="h-4 w-4" />
        Add question
      </Button>
    </div>
  )
}
