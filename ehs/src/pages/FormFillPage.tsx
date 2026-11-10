/**
 * Filling in one form.
 *
 * Answers are saved to the server as they are entered rather than held until
 * submit: an inspection walked around a warehouse takes twenty minutes, and a
 * phone that sleeps or a browser that reloads should not cost that. The
 * submission exists as a draft from the moment it is started, which is what
 * makes that possible.
 *
 * Saving is debounced and never blocks typing. A failed save leaves a visible
 * marker rather than a dialog — the person is holding a phone at a rack, and a
 * modal they have to dismiss to keep working is worse than a line of red text
 * they can act on when they get back to signal.
 */
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, CloudOff } from 'lucide-react'
import { useTabTitle } from '@uniops/shell'
import { api, ApiError } from '@/lib/api'
import { ehsRoutes } from '@/app/routes'
import { FormRenderer, looksLikeFailure, type FormValues, type Question } from '@/components/FormRenderer'
import { LinkedActions } from '@/components/LinkedActions'
import { ActionHistory } from '@/components/ActionHistory'
import { useMyProfile } from '@/hooks/useMyProfile'
import { useEhsAuth } from '@/store/auth'
import { todayForInput } from '@/lib/formatDate'
import { formatAreaPath } from '@/lib/areaPath'
import type { CompressedImage } from '@/lib/imageCompress'

interface Submission {
  id: string
  submission_no: string
  status: string
  schema_snapshot: Question[]
  location_path: string | null
  submitted_at: string | null
  submitted_by_name: string | null
  source_type: string | null
  source_id: string | null
  source_ref: string | null
  failed_count: number
}

interface Answer {
  question_key: string
  question_type: string
  value_text: string | null
  value_number: number | null
  value_date: string | null
  value_ids: string[]
  value_labels: string[]
  is_failure: boolean
}

/** Server answer rows back into the shapes the renderer edits. */
function toValues(answers: Answer[]): FormValues {
  const out: FormValues = {}
  for (const a of answers) {
    if (a.value_ids.length) {
      out[a.question_key] = a.value_labels.length
        ? { id: a.value_ids[0], label: a.value_labels[0] }
        : a.value_ids
    } else if (a.value_number !== null) {
      out[a.question_key] = String(a.value_number)
    } else if (a.value_date) {
      out[a.question_key] = a.value_date
    } else if (a.value_text !== null) {
      out[a.question_key] = a.value_text
    }
  }
  return out
}

export default function FormFillPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const profile = useMyProfile()

  const submission = useQuery({
    queryKey: ['form-submission', id],
    queryFn: () => api.get<Submission>(`/api/v1/forms/submissions/${id}`),
  })
  const answers = useQuery({
    queryKey: ['form-submission', id, 'answers'],
    queryFn: () => api.get<Answer[]>(`/api/v1/forms/submissions/${id}/answers`),
  })

  useTabTitle(ehsRoutes, submission.data?.submission_no)

  const [values, setValues] = useState<FormValues>({})
  const [photos, setPhotos] = useState<Record<string, CompressedImage[]>>({})
  const [loaded, setLoaded] = useState(false)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'failed'>('idle')
  const [error, setError] = useState<string | null>(null)

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pending = useRef<FormValues>({})

  // ★ Round 7, TC-01: "the meeting minutes contains Meeting Date Not answered
  // and other not answered fields that are answered on the form." Another tab
  // reads these answers — the committee meeting the minutes belong to — and it
  // stays open while the form is filled in. It had read the form while it was
  // blank and kept that copy, so it showed every answer as missing.
  const readersOfTheAnswers = () => {
    queryClient.invalidateQueries({ queryKey: ['jhsc-minutes'] })
  }

  const flush = async () => {
    const batch = pending.current
    pending.current = {}
    if (Object.keys(batch).length === 0) return
    setSaveState('saving')
    try {
      await api.put(`/api/v1/forms/submissions/${id}/answers`, { values: batch })
      setSaveState('saved')
      readersOfTheAnswers()
    } catch (e) {
      // Put the batch back so the next save retries it rather than losing it.
      pending.current = { ...batch, ...pending.current }
      setSaveState('failed')
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }

  const change = (key: string, value: unknown) => {
    setValues((v) => ({ ...v, [key]: value }))
    pending.current[key] = value
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(flush, 900)
  }

  const me = useEhsAuth((st) => st.user)

  useEffect(() => {
    // ★ A failed answers request leaves `data` undefined exactly as an empty
    // form does, and the seeding below treats "no rows" as "nobody has filled
    // this in yet" — so a form that already had an inspector and a date would
    // have had today's date and whoever opened it queued over the top, and the
    // first edit would have written them. Raised in review by uniops-41. The
    // same shape as the four `?? []` reads found in round 4, one level deeper:
    // here the empty list does not just read as a fact, it overwrites one.
    if (loaded || answers.isLoading || answers.isError || !submission.data) return
    const rows = answers.data ?? []
    const next = toValues(rows)

    // A form opened for the first time gets today's date in its date questions —
    // and the same value goes into the pending batch, so what is shown is what
    // gets stored. A default that only shows in the input is worse than none:
    // the person submits without touching it and the answer is empty.
    //
    // It is queued, not saved: opening a draft must not write to it. Scheduled
    // inspections are visible to more people than will fill them, and a save on
    // open would stamp the date someone happened to look, under their name, and
    // then never re-seed for the person who actually fills it in the next day.
    // Queued is enough — the first edit and the submit both flush.
    if (rows.length === 0 && submission.data.status !== 'submitted') {
      const today = todayForInput()
      // ★ Round 5, TC-06: "the logged in user should be the default inspector
      // when opening inspection forms."
      //
      // Only the first single-person question, and only while the form is
      // untouched. A form can ask for more than one person — who carried it
      // out, who was present — and filling them all with whoever opened it
      // would be worse than filling none. The first one is "who is doing
      // this" by convention on every template HSE has shown us; a form where
      // it is not can have it changed, which is the point of a default.
      let inspectorSeeded = false
      for (const q of submission.data.schema_snapshot ?? []) {
        if (q.type === 'date' && next[q.key] === undefined) {
          next[q.key] = today
          pending.current[q.key] = today
        }
        if (q.type === 'person' && !inspectorSeeded && next[q.key] === undefined && me) {
          inspectorSeeded = true
          const value = [{ id: me.id, label: me.full_name }]
          next[q.key] = value
          pending.current[q.key] = value
        }
      }
    }

    setValues(next)
    setLoaded(true)
  }, [loaded, answers.isLoading, answers.data, submission.data, me])

  // A form closed mid-edit still saves what was typed.
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  const submit = useMutation({
    mutationFn: async () => {
      if (timer.current) clearTimeout(timer.current)
      await flush()
      // ★ flush() reports a failure rather than raising, so the debounced
      // autosave can put the batch back and retry. Submitting regardless was
      // the fault: the form would be marked submitted — and submitted forms are
      // immutable — while what was typed never left the browser. Nothing is
      // lost by stopping here; the batch is still pending and the next save
      // sends it.
      if (Object.keys(pending.current).length > 0) {
        throw new ApiError(
          0,
          'What you typed has not been saved yet, so this has not been ' +
          'submitted. Check your connection and try again — nothing has been lost.',
          null)
      }

      // Photos upload before the submit call, because a submitted form is
      // immutable — after it, there is nowhere for them to go.
      for (const [key, list] of Object.entries(photos)) {
        for (const photo of list) {
          if (photo.uploaded) continue
          const blob = await (await fetch(photo.dataUrl)).blob()
          const form = new FormData()
          form.append('file', blob, photo.name)
          await api.upload(
            `/api/v1/forms/submissions/${id}/attachments?question_key=${encodeURIComponent(key)}`,
            form)
          photo.uploaded = true
        }
      }
      return api.post<Submission>(`/api/v1/forms/submissions/${id}/submit`, {})
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['form-submission', id] })
      queryClient.invalidateQueries({ queryKey: ['form-submissions'] })
      readersOfTheAnswers()
      navigate('/forms/submissions')
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  if (submission.isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  if (submission.isError) {
    return <p className="p-4 text-sm text-danger-700">{(submission.error as ApiError).message}</p>
  }

  // ★ The answers, separately. The form itself loaded, so the page renders —
  // but an empty form and a form whose answers could not be fetched look
  // identical, and this one can be typed into. Refusing to show the questions is
  // the only honest option: anything typed would be written over answers that
  // are there and cannot be seen.
  if (answers.isError) {
    return (
      <div className="p-4">
        <p className="rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">
          What has already been filled in on this form could not be loaded, so it
          is not being shown — filling it in now would write over answers that
          are there. Try again in a moment.
        </p>
      </div>
    )
  }

  const sub = submission.data!
  const done = sub.status === 'submitted'
  const schema = sub.schema_snapshot ?? []
  const failing = schema.filter((q) => looksLikeFailure(q, values[q.key]))
  const missing = schema.filter((q) => q.required && !values[q.key])

  return (
    <div className="p-4 pb-28">
      <header className="mb-4">
        <p className="font-mono text-xs text-neutral-500">{sub.submission_no}</p>
        {/* ★ What this form is for. Opened from a meeting, an unfilled set of
            minutes used to be indistinguishable from a fresh blank form — the
            only thing on screen was this reference number, and checking it
            meant comparing it against the other screen. */}
        {/* ★ The item this check is for, which is what "with the name of the
            station on the form automatic" asks for. Taken from the record rather
            than written into an answer: a template that happens not to have a
            "which item" question would otherwise lose it. */}
        {sub.source_type === 'asset' && (
          <p className="mt-0.5 text-sm text-neutral-700">
            Inspection of <span className="font-medium text-neutral-900">
              {sub.source_ref ?? 'a piece of equipment'}
            </span>
          </p>
        )}
        {sub.source_type === 'jhsc' && sub.source_id && (
          <p className="mt-0.5 text-sm text-neutral-700">
            Minutes of{' '}
            <Link to={`/jhsc/meetings/${sub.source_id}`}
                  className="font-medium text-primary-700 hover:underline">
              {sub.source_ref ?? 'the committee meeting'}
            </Link>
          </p>
        )}
        {sub.location_path && (
          <p className="mt-0.5 text-sm text-neutral-600">
            {formatAreaPath(sub.location_path)}
          </p>
        )}
        {done && (
          <p className="mt-2 rounded-lg bg-primary-50 px-3 py-2 text-xs text-primary-800">
            Submitted by {sub.submitted_by_name ?? 'someone'}
            {sub.failed_count > 0 && ` · ${sub.failed_count} failed check(s) raised an action`}
          </p>
        )}
        {/* ★ Which actions, not how many. "3 failed check(s) raised an action"
            named none of them, so the only route to the work was to guess at the
            actions list and match by title. */}
        {done && sub.failed_count > 0 && (
          <LinkedActions
            sourceType="inspection" sourceId={id ?? ''}
            title="Raised by the failed checks"
            emptyNote="The failed checks raised nothing — tell us, because they should have." />
        )}
        {done && sub.failed_count > 0 && (
          <ActionHistory sourceType="inspection" sourceId={id} />
        )}
      </header>

      <FormRenderer
        schema={schema}
        values={values}
        onChange={change}
        photos={photos}
        onPhotosChange={(k, next) => setPhotos((p) => ({ ...p, [k]: next }))}
        savedSignature={profile.data?.signature_image ?? null}
        disabled={done}
      />

      {error && (
        <p className="mb-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>
      )}

      {!done && (
        <div className="fixed inset-x-0 bottom-0 border-t border-neutral-200 bg-white/95 p-3 backdrop-blur">
          <div className="mx-auto flex max-w-2xl items-center gap-3">
            <span className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-neutral-500">
              {saveState === 'saving' && 'Saving…'}
              {saveState === 'saved' && (<><Check className="h-3.5 w-3.5 text-primary-600" aria-hidden />Saved</>)}
              {saveState === 'failed' && (<><CloudOff className="h-3.5 w-3.5 text-danger-600" aria-hidden />Not saved — will retry</>)}
              {saveState === 'idle' && failing.length > 0 && (
                <><AlertTriangle className="h-3.5 w-3.5 text-danger-600" aria-hidden />
                  {failing.length} failed check{failing.length > 1 ? 's' : ''}</>
              )}
            </span>
            <button
              type="button"
              disabled={submit.isPending || missing.length > 0}
              onClick={() => { if (!submit.isPending && missing.length === 0) { setError(null); submit.mutate() } }}
              className="min-h-[44px] flex-[1.2] rounded-lg bg-primary-600 px-4 text-sm font-semibold text-white disabled:opacity-50"
            >
              {submit.isPending ? 'Submitting…'
                : missing.length > 0 ? `${missing.length} left`
                : 'Submit'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
