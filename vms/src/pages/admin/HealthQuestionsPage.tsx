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
import {
  AlertTriangle, CheckCircle2, Loader2, Plus, RotateCcw, X,
} from 'lucide-react'
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
          <h2 className="text-base font-semibold text-neutral-900">Health questionnaire</h2>
          <p className="mt-0.5 text-xs text-neutral-500">
            Edit the yes/no questions that drive the health declaration form for
            GMP / Lab visits. Question text is snapshotted into each declaration
            at submit time — past declarations stay readable after edits.
          </p>
        </div>
        <button
          onClick={onSave}
          disabled={!canSave || save.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
        >
          {save.isPending
            ? <Loader2 className="h-4 w-4 animate-spin" />
            : <CheckCircle2 className="h-4 w-4" />}
          Save
        </button>
      </div>

      {/* Version + bump */}
      <div className="mt-4 flex items-center gap-2 text-sm">
        <label className="text-neutral-600">Template version:</label>
        <input
          type="number"
          min="1"
          value={current.version}
          onChange={(e) => setVersion(parseInt(e.target.value) || 1)}
          className="w-20 rounded-md border border-neutral-300 px-2 py-1 text-sm"
        />
        <button
          type="button"
          onClick={() => setVersion(current.version + 1)}
          className="inline-flex items-center gap-1 text-xs text-neutral-500 hover:text-neutral-700"
        >
          <RotateCcw className="h-3 w-3" />
          Bump
        </button>
      </div>

      {/* Warnings */}
      {removedIds.length > 0 && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
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
        <p className="mt-3 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">
          Duplicate question ids: {[...dupSet].join(', ')}
        </p>
      )}
      {save.error && (
        <p className="mt-3 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">
          {save.error.message}
        </p>
      )}
      {save.isSuccess && !dirty && (
        <p className="mt-3 rounded-md bg-success-50 px-3 py-2 text-xs text-success-600">
          Template saved. New visits will use this version.
        </p>
      )}

      {/* Questions */}
      <div className="mt-5 space-y-2">
        {isLoading && (
          <p className="text-sm text-neutral-400">Loading…</p>
        )}
        {current.questions.map((q, idx) => (
          <div
            key={idx}
            className={
              'rounded-md border bg-white p-3 ' +
              (dupSet.has(q.id) ? 'border-red-300' : 'border-neutral-200')
            }
          >
            <div className="grid grid-cols-1 gap-2 md:grid-cols-[180px_1fr_120px_auto]">
              <input
                value={q.id}
                onChange={(e) => updateQuestion(idx, { id: e.target.value })}
                placeholder="id"
                className="rounded-md border border-neutral-300 px-2 py-1.5 text-xs font-mono"
              />
              <input
                value={q.text}
                onChange={(e) => updateQuestion(idx, { text: e.target.value })}
                placeholder="Question text"
                className="rounded-md border border-neutral-300 px-2 py-1.5 text-sm"
              />
              <select
                value={q.fail_on}
                onChange={(e) => updateQuestion(idx, { fail_on: e.target.value })}
                className="rounded-md border border-neutral-300 px-2 py-1.5 text-sm"
              >
                <option value="yes">fail on YES</option>
                <option value="no">fail on NO</option>
              </select>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => move(idx, -1)}
                  disabled={idx === 0}
                  className="rounded p-1 text-neutral-400 hover:bg-neutral-100 disabled:opacity-30"
                >▲</button>
                <button
                  onClick={() => move(idx, 1)}
                  disabled={idx === current.questions.length - 1}
                  className="rounded p-1 text-neutral-400 hover:bg-neutral-100 disabled:opacity-30"
                >▼</button>
                <button
                  onClick={() => removeQuestion(idx)}
                  className="rounded p-1 text-danger-600 hover:bg-danger-50"
                ><X className="h-3.5 w-3.5" /></button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <button
        onClick={addQuestion}
        className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-dashed border-neutral-300 px-3 py-2 text-sm text-neutral-600 hover:bg-neutral-50"
      >
        <Plus className="h-4 w-4" />
        Add question
      </button>
    </div>
  )
}
