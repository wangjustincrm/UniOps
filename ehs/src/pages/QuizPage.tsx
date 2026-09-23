/**
 * Training quizzes: building one, and taking one on a phone.
 *
 * Two audiences in one file because they are two views of the same object and
 * splitting them would mean keeping two route trees in step. What is *not*
 * shared is the data: the taking view fetches `/questions`, which carries no
 * answers, and the authoring view fetches `/answer-key`, which does. Neither
 * component can accidentally render the other's payload, because they are
 * different requests with different shapes.
 *
 * The taking view is the one that matters. It is used standing up, on a phone,
 * at the end of a session — so one question per screen, big targets, and the
 * result explains every wrong answer rather than just scoring it. A quiz that
 * only tells you that you failed is a filter, not training.
 */
import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { Check, ClipboardList, Plus, QrCode, Trash2, X } from 'lucide-react'
import { api } from '@/lib/api'
import { Pagination, SearchBox, useListView } from '@/components/ListView'
import { formatDate } from '@/lib/formatDate'
import QrPanel from '@/components/QrPanel'
import { usePermissions } from '@/hooks/usePermissions'
import {
  Card, Empty, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle,
} from '@/components/ui'

interface Quiz {
  id: string; course_id: string | null; title: string; intro: string | null
  pass_mark_percent: number; max_attempts: number | null
  is_active: boolean; qr_token: string | null; question_count: number
}
interface LearnerQuestion {
  id: string; seq: number; prompt: string; kind: string
  options: { key: string; label: string }[]; points: number
}
interface AuthorQuestion extends LearnerQuestion {
  correct: string[]; explanation: string | null
}
interface Marked {
  question_id: string; prompt: string; correct: boolean
  your_answer: string[]; right_answer: string[]; explanation: string | null
}
interface Attempt {
  id: string; score_percent: string; points_scored: number; points_possible: number
  passed: boolean; pass_mark_percent: number; submitted_at: string
  training_record_id: string | null; marked: Marked[]
}
interface Results {
  people: number; passed: number; pass_rate: number | null
  average_score: number | null; total_attempts: number
  by_question: { question_id: string; prompt: string; got_it_wrong: number; of: number }[]
}
interface Course { id: string; code: string; name: string }

// ── List ─────────────────────────────────────────────────────────────────────

export default function QuizPage() {
  const navigate = useNavigate()
  const { can } = usePermissions()
  const mayWrite = can('ehs.training.write')
  const [creating, setCreating] = useState(false)

  const quizzes = useQuery({
    queryKey: ['quizzes'],
    queryFn: () => api.get<Quiz[]>('/api/v1/quizzes'),
  })
  const list = useListView(quizzes.data, (q) => [q.title, q.intro])

  return (
    <div className="p-4">
      <PageHeader title="Quizzes"
                  subtitle="Answered on a phone at the end of a session. A pass writes the training record.">
        {mayWrite && (
          <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            New quiz
          </button>
        )}
      </PageHeader>

      {creating && <NewQuizForm onClose={() => setCreating(false)} />}


      <SearchBox value={list.query} onChange={list.setQuery}

                 placeholder="Search by title" />


      {quizzes.data?.length === 0 && !creating ? (
        <Empty>
          None yet. A quiz is attached to a course, and passing it puts a record
          in the person&rsquo;s file — the same record a classroom session would.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {list.pageRows.map((q) => (
            <li key={q.id}>
              <Card>
                <div className="flex flex-wrap items-start gap-3">
                  <ClipboardList className="mt-0.5 h-5 w-5 shrink-0 text-primary-600" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">{q.title}</span>
                    <span className="mt-0.5 block text-xs text-neutral-500">
                      {q.question_count} question{q.question_count === 1 ? '' : 's'} ·
                      pass at {q.pass_mark_percent}%
                      {q.max_attempts && ` · ${q.max_attempts} attempts`}
                    </span>
                  </span>
                  <span className="flex shrink-0 gap-2">
                    <button type="button" className={secondaryButton}
                            onClick={() => navigate(`/quizzes/${q.id}/take`)}>
                      Take it
                    </button>
                    {mayWrite && (
                      <button type="button" className={secondaryButton}
                              onClick={() => navigate(`/quizzes/${q.id}/edit`)}>
                        Edit
                      </button>
                    )}
                  </span>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <Pagination page={list.page} pageCount={list.pageCount} total={list.total}
                  onPage={list.setPage} noun="quizzes" />
    </div>
  )
}

function NewQuizForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [courseId, setCourseId] = useState('')
  const [passMark, setPassMark] = useState('80')

  const courses = useQuery({
    queryKey: ['courses'],
    queryFn: () => api.get<Course[]>('/api/v1/training/courses'),
  })

  const create = useMutation({
    mutationFn: () => api.post<Quiz>('/api/v1/quizzes', {
      title, course_id: courseId || null, pass_mark_percent: Number(passMark),
    }),
    onSuccess: (q) => {
      queryClient.invalidateQueries({ queryKey: ['quizzes'] })
      onClose()
      navigate(`/quizzes/${q.id}/edit`)
    },
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">New quiz</h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="sm:col-span-2">
          <Field label="Title">
            <input className={inputClass} value={title}
                   onChange={(e) => setTitle(e.target.value)}
                   placeholder="WHMIS 2015 — end of session check" />
          </Field>
        </div>
        <Field label="Pass mark %">
          <input className={inputClass} inputMode="numeric" value={passMark}
                 onChange={(e) => setPassMark(e.target.value.replace(/[^0-9]/g, ''))} />
        </Field>
        <div className="sm:col-span-3">
          <Field label="Course"
                 hint="A pass writes a record against this course. Leave blank for a quiz that is only practice.">
            <select className={inputClass} value={courseId}
                    onChange={(e) => setCourseId(e.target.value)}>
              <option value="">No course — practice only</option>
              {courses.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
        </div>
      </div>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!title.trim() || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Creating…' : 'Create and add questions'}
        </button>
      </div>
    </Card>
  )
}

// ── Taking ───────────────────────────────────────────────────────────────────

/**
 * One question per screen. Used standing up, on a phone, so the targets are
 * large and there is nothing else on the page.
 */
export function TakeQuizPage() {
  const { id = '' } = useParams()
  const [index, setIndex] = useState(0)
  const [answers, setAnswers] = useState<Record<string, string[]>>({})
  const [result, setResult] = useState<Attempt | null>(null)

  const quiz = useQuery({
    queryKey: ['quiz', id],
    queryFn: () => api.get<Quiz>(`/api/v1/quizzes/${id}`),
  })
  // Deliberately the learner endpoint. It carries no answers.
  const questions = useQuery({
    queryKey: ['quiz-questions', id],
    queryFn: () => api.get<LearnerQuestion[]>(`/api/v1/quizzes/${id}/questions`),
  })

  const submit = useMutation({
    mutationFn: () => api.post<Attempt>(`/api/v1/quizzes/${id}/attempts`, { answers }),
    onSuccess: setResult,
  })

  if (questions.isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  const rows = questions.data ?? []
  if (rows.length === 0) {
    return <div className="p-4"><Empty>This quiz has no questions yet.</Empty></div>
  }

  if (result) return <ResultView attempt={result} quizId={id} />

  const q = rows[index]
  const chosen = answers[q.id] ?? []
  const multi = q.kind === 'multi'
  const last = index === rows.length - 1
  const answeredAll = rows.every((r) => (answers[r.id] ?? []).length > 0)

  const toggle = (key: string) => {
    setAnswers((a) => {
      const current = a[q.id] ?? []
      if (multi) {
        return { ...a, [q.id]: current.includes(key)
          ? current.filter((k) => k !== key) : [...current, key] }
      }
      return { ...a, [q.id]: [key] }
    })
  }

  return (
    <div className="mx-auto max-w-xl p-4">
      <p className="text-xs font-semibold uppercase tracking-wider text-neutral-400">
        {quiz.data?.title}
      </p>
      <div className="mt-2 mb-4 flex items-center gap-2">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-neutral-200">
          <div className="h-full rounded-full bg-primary-500 transition-all"
               style={{ width: `${((index + 1) / rows.length) * 100}%` }} aria-hidden />
        </div>
        <span className="shrink-0 text-xs tabular-nums text-neutral-500">
          {index + 1} / {rows.length}
        </span>
      </div>

      <h1 className="mb-1 text-lg font-semibold leading-snug text-neutral-900">
        {q.prompt}
      </h1>
      {multi && (
        <p className="mb-3 text-xs text-neutral-500">
          Pick every one that applies — a partly right answer scores nothing here.
        </p>
      )}

      <ul className="mt-3 flex flex-col gap-2">
        {q.options.map((o) => {
          const picked = chosen.includes(o.key)
          return (
            <li key={o.key}>
              <button type="button" onClick={() => toggle(o.key)}
                      aria-pressed={picked}
                      className={
                        'flex min-h-[56px] w-full items-center gap-3 rounded-xl border px-4 text-left text-sm transition-colors ' +
                        (picked
                          ? 'border-primary-500 bg-primary-50 font-medium text-primary-900'
                          : 'border-neutral-300 bg-white text-neutral-800 hover:border-neutral-400')
                      }>
                <span className={
                  'flex h-6 w-6 shrink-0 items-center justify-center border ' +
                  (multi ? 'rounded' : 'rounded-full') + ' ' +
                  (picked ? 'border-primary-600 bg-primary-600 text-white'
                          : 'border-neutral-300')
                }>
                  {picked && <Check className="h-4 w-4" aria-hidden />}
                </span>
                {o.label}
              </button>
            </li>
          )
        })}
      </ul>

      <ErrorNote error={submit.error} />

      <div className="mt-5 flex items-center justify-between gap-2">
        <button type="button" className={secondaryButton} disabled={index === 0}
                onClick={() => setIndex((i) => i - 1)}>
          Back
        </button>
        {last ? (
          <button type="button" className={primaryButton}
                  disabled={!answeredAll || submit.isPending}
                  onClick={() => { if (!submit.isPending) submit.mutate() }}>
            {submit.isPending ? 'Marking…' : 'Finish'}
          </button>
        ) : (
          <button type="button" className={primaryButton}
                  disabled={chosen.length === 0}
                  onClick={() => setIndex((i) => i + 1)}>
            Next
          </button>
        )}
      </div>
      {last && !answeredAll && (
        <p className="mt-2 text-right text-xs text-neutral-500">
          Every question needs an answer before you can finish.
        </p>
      )}
    </div>
  )
}

/**
 * The result, with every wrong answer explained.
 *
 * A quiz that only says you failed is a filter. The explanation is the reason
 * this is worth running at the end of a session at all.
 */
function ResultView({ attempt, quizId }: { attempt: Attempt; quizId: string }) {
  const navigate = useNavigate()
  const wrong = attempt.marked.filter((m) => !m.correct)

  return (
    <div className="mx-auto max-w-xl p-4">
      <div className={
        'rounded-xl p-5 text-center ' +
        (attempt.passed ? 'bg-success-50' : 'bg-warning-50')
      }>
        <p className={`font-mono text-4xl font-bold tabular-nums ${
          attempt.passed ? 'text-success-700' : 'text-warning-700'}`}>
          {Math.round(Number(attempt.score_percent))}%
        </p>
        <p className={`mt-1 text-sm font-semibold ${
          attempt.passed ? 'text-success-700' : 'text-warning-700'}`}>
          {attempt.passed ? 'Passed' : `Not this time — the pass mark is ${attempt.pass_mark_percent}%`}
        </p>
        <p className="mt-0.5 text-xs text-neutral-600">
          {attempt.points_scored} of {attempt.points_possible} points
        </p>
        {attempt.training_record_id && (
          <p className="mt-2 text-xs font-medium text-success-700">
            Recorded in your training file.
          </p>
        )}
      </div>

      {wrong.length > 0 && (
        <>
          <SectionTitle className="mb-2 mt-5">What to look at again</SectionTitle>
          <ul className="flex flex-col gap-2">
            {wrong.map((m) => (
              <li key={m.question_id}>
                <Card>
                  <p className="text-sm font-medium text-neutral-900">{m.prompt}</p>
                  <p className="mt-1 text-xs text-neutral-600">
                    You answered {m.your_answer.join(', ') || '—'};
                    the answer is {m.right_answer.join(', ')}.
                  </p>
                  {m.explanation && (
                    <p className="mt-1.5 rounded-lg bg-neutral-50 px-3 py-2 text-sm text-neutral-700">
                      {m.explanation}
                    </p>
                  )}
                </Card>
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="mt-5 flex justify-center gap-2">
        {!attempt.passed && (
          <button type="button" className={primaryButton}
                  onClick={() => window.location.reload()}>
            Try again
          </button>
        )}
        <button type="button" className={secondaryButton}
                onClick={() => navigate(`/quizzes`)}>
          Done
        </button>
      </div>
      <p className="mt-3 text-center text-xs text-neutral-500">
        Every attempt is kept — a trainer can see which question the room found
        hardest, which is the useful part.
      </p>
      <input type="hidden" value={quizId} readOnly />
    </div>
  )
}

// ── Authoring ────────────────────────────────────────────────────────────────

export function EditQuizPage() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()

  const quiz = useQuery({
    queryKey: ['quiz', id],
    queryFn: () => api.get<Quiz>(`/api/v1/quizzes/${id}`),
  })
  // The authoring endpoint, named so it cannot be wired into the taking view
  // by accident.
  const questions = useQuery({
    queryKey: ['quiz-key', id],
    queryFn: () => api.get<AuthorQuestion[]>(`/api/v1/quizzes/${id}/answer-key`),
  })
  const results = useQuery({
    queryKey: ['quiz-results', id],
    queryFn: () => api.get<Results>(`/api/v1/quizzes/${id}/results`),
  })

  const [draft, setDraft] = useState<AuthorQuestion[] | null>(null)
  const rows = draft ?? questions.data ?? []

  const save = useMutation({
    mutationFn: () => api.put(`/api/v1/quizzes/${id}/questions`, {
      questions: rows.map((q) => ({
        prompt: q.prompt, kind: q.kind, options: q.options,
        correct: q.correct, explanation: q.explanation, points: q.points,
      })),
    }),
    onSuccess: () => {
      setDraft(null)
      queryClient.invalidateQueries({ queryKey: ['quiz-key', id] })
      queryClient.invalidateQueries({ queryKey: ['quizzes'] })
    },
  })

  const set = (i: number, patch: Partial<AuthorQuestion>) =>
    setDraft(rows.map((r, k) => k === i ? { ...r, ...patch } : r))

  const [presenting, setPresenting] = useState(false)
  const link = quiz.data?.qr_token
    ? `${window.location.origin}/qr/${quiz.data.qr_token}` : null

  return (
    <div className="p-4">
      <PageHeader title={quiz.data?.title ?? 'Quiz'}
                  subtitle={`Pass at ${quiz.data?.pass_mark_percent ?? 80}%`} />

      {/* The code is shown on its own, never on this page.
          This is the authoring view: the questions and their answers are below.
          Putting the code here meant projecting the code meant projecting the
          answer key — which is what the second acceptance round caught. */}
      {link && (
        <Card className="mb-4">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-neutral-900">
            <QrCode className="h-4 w-4 text-neutral-400" aria-hidden />
            The code to put on the screen
          </p>
          <p className="mt-1 text-xs text-neutral-500">
            Opens on its own, with nothing else on it. The answers are on this
            page, so this page is not the one to put on the projector.
          </p>
          <button type="button" onClick={() => setPresenting(true)}
                  className={`${primaryButton} mt-2.5`}>
            Show the code to the room
          </button>
        </Card>
      )}

      {presenting && quiz.data && (
        <QuizCodeScreen quiz={quiz.data} onClose={() => setPresenting(false)} />
      )}

      {(results.data?.people ?? 0) > 0 && (
        <Card className="mb-4">
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <Stat label="Passed" value={`${results.data?.passed} of ${results.data?.people}`} />
            <Stat label="Average" value={`${results.data?.average_score ?? '—'}%`} />
            <Stat label="Attempts" value={String(results.data?.total_attempts ?? 0)} />
          </div>
          <SectionTitle className="mb-1 mt-3">Hardest questions</SectionTitle>
          <ul className="flex flex-col gap-1">
            {results.data?.by_question.slice(0, 3).map((row) => (
              <li key={row.question_id} className="flex items-baseline gap-2 text-sm">
                <span className={`font-mono font-semibold tabular-nums ${
                  row.got_it_wrong > 0 ? 'text-warning-700' : 'text-neutral-400'}`}>
                  {row.got_it_wrong}/{row.of}
                </span>
                <span className="min-w-0 flex-1 truncate text-neutral-700">{row.prompt}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-neutral-500">
            A question everybody got wrong is a gap in the training, not in the
            people.
          </p>
        </Card>
      )}

      <WhoTookIt quizId={id} />

      <SectionTitle className="mb-2">Questions</SectionTitle>

      {rows.length === 0 && (
        <Empty>No questions yet. Add one below.</Empty>
      )}

      <ol className="flex flex-col gap-2">
        {rows.map((q, i) => (
          <li key={i}>
            <Card>
              <div className="flex items-start gap-2">
                <span className="mt-2.5 font-mono text-xs text-neutral-400">{i + 1}</span>
                <div className="grid min-w-0 flex-1 gap-2">
                  <Field label="Question">
                    <input className={inputClass} value={q.prompt}
                           onChange={(e) => set(i, { prompt: e.target.value })} />
                  </Field>
                  <Field label="Type">
                    <select className={inputClass} value={q.kind}
                            onChange={(e) => set(i, { kind: e.target.value, correct: [] })}>
                      <option value="single">One right answer</option>
                      <option value="multi">Several right answers</option>
                      <option value="true_false">True or false</option>
                    </select>
                  </Field>
                  <div>
                    <p className="mb-1 text-xs font-medium text-neutral-700">
                      Options — tick the right one{q.kind === 'multi' ? 's' : ''}
                    </p>
                    <ul className="flex flex-col gap-1">
                      {q.options.map((o, oi) => (
                        <li key={oi} className="flex items-center gap-2">
                          <input type={q.kind === 'multi' ? 'checkbox' : 'radio'}
                                 checked={q.correct.includes(o.key)}
                                 onChange={() => set(i, {
                                   correct: q.kind === 'multi'
                                     ? (q.correct.includes(o.key)
                                        ? q.correct.filter((c) => c !== o.key)
                                        : [...q.correct, o.key])
                                     : [o.key],
                                 })} />
                          <input className={`${inputClass} flex-1`} value={o.label}
                                 onChange={(e) => set(i, {
                                   options: q.options.map((x, xi) =>
                                     xi === oi ? { ...x, label: e.target.value } : x),
                                 })} />
                          <button type="button" aria-label="Remove option"
                                  onClick={() => set(i, {
                                    options: q.options.filter((_, xi) => xi !== oi),
                                    correct: q.correct.filter((c) => c !== o.key),
                                  })}
                                  className="rounded p-1.5 text-neutral-400 hover:text-danger-600">
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </li>
                      ))}
                    </ul>
                    <button type="button" className={`${secondaryButton} mt-1.5`}
                            onClick={() => set(i, {
                              options: [...q.options, {
                                key: String.fromCharCode(97 + q.options.length),
                                label: '',
                              }],
                            })}>
                      <Plus className="h-3.5 w-3.5" aria-hidden />
                      Option
                    </button>
                  </div>
                  <Field label="Explanation"
                         hint="Shown when somebody gets it wrong — this is the teaching part">
                    <input className={inputClass} value={q.explanation ?? ''}
                           onChange={(e) => set(i, { explanation: e.target.value })} />
                  </Field>
                </div>
                <button type="button" aria-label="Remove question"
                        onClick={() => setDraft(rows.filter((_, k) => k !== i))}
                        className="mt-1.5 rounded p-1.5 text-neutral-400 hover:bg-danger-50 hover:text-danger-600">
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </Card>
          </li>
        ))}
      </ol>

      <ErrorNote error={save.error} />

      <div className="mt-3 flex flex-wrap justify-between gap-2">
        <button type="button" className={secondaryButton}
                onClick={() => setDraft([...rows, {
                  id: '', seq: rows.length + 1, prompt: '', kind: 'single',
                  options: [{ key: 'a', label: '' }, { key: 'b', label: '' }],
                  correct: [], explanation: null, points: 1,
                }])}>
          <Plus className="h-4 w-4" aria-hidden />
          Add a question
        </button>
        <button type="button" className={primaryButton}
                disabled={draft === null || save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}>
          {save.isPending ? 'Saving…' : 'Save the questions'}
        </button>
      </div>
      <p className="mt-2 text-right text-xs text-neutral-500">
        Questions cannot be changed once anybody has answered — their scores
        would stop meaning the same thing as everyone else&rsquo;s.
      </p>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-neutral-500">{label}</p>
      <p className="font-mono text-lg font-bold tabular-nums text-neutral-900">{value}</p>
    </div>
  )
}

/**
 * The code, full screen, and nothing else.
 *
 * What gets projected. Deliberately carries the quiz title and the code and no
 * question — the authoring page has the answers on it, and showing the code
 * from there showed the class the answer key.
 */
interface AttemptRow {
  id: string; user_id: string | null; user_name: string
  submitted_at: string; score_percent: string; passed: boolean
}

/**
 * Who took the quiz and how they did.
 *
 * The totals were on screen — how many passed, the average, the hardest
 * questions — and who was behind them was not. HSE reported it plainly: they
 * could not see the results of completed quizzes. The aggregate answers
 * "did the training land"; this answers "who still needs it", which is the
 * question that produces a training record.
 *
 * The endpoint has been there since the quiz was built and nothing called it.
 */
function WhoTookIt({ quizId }: { quizId: string }) {
  const attempts = useQuery({
    queryKey: ['quiz-attempts', quizId],
    queryFn: () => api.get<AttemptRow[]>(`/api/v1/quizzes/${quizId}/attempts`),
  })

  if (!attempts.data?.length) return null

  return (
    <Card className="mb-4">
      <SectionTitle className="mb-2">Who took it</SectionTitle>
      <ul className="flex flex-col gap-1">
        {attempts.data.map((a) => (
          <li key={a.id} className="flex flex-wrap items-baseline gap-x-2.5 text-sm">
            <span className="min-w-0 flex-1 text-neutral-800">{a.user_name}</span>
            <span className="font-mono tabular-nums text-neutral-700">
              {Number(a.score_percent)}%
            </span>
            <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
              a.passed ? 'bg-success-50 text-success-700' : 'bg-danger-50 text-danger-700'}`}>
              {a.passed ? 'Passed' : 'Did not pass'}
            </span>
            <span className="text-xs text-neutral-500">{formatDate(a.submitted_at)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-neutral-500">
        Every attempt is kept, not only the best one — a pass on the third go is
        a different training record from a pass on the first.
      </p>
    </Card>
  )
}


function QuizCodeScreen({ quiz, onClose }: { quiz: Quiz; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-6 bg-white p-6"
         role="dialog" aria-modal="true" aria-label="Scan to take the quiz">
      <h2 className="text-center text-2xl font-bold tracking-tight text-neutral-900">
        {quiz.title}
      </h2>
      <QrPanel token={quiz.qr_token} caption="Scan to take this quiz" size={280} />
      <p className="text-sm text-neutral-500">Answer on your own phone.</p>
      <button type="button" onClick={onClose} className={secondaryButton}>
        Done
      </button>
    </div>
  )
}
