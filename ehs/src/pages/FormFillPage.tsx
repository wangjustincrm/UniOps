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
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, CloudOff } from 'lucide-react'
import { useTabTitle } from '@uniops/shell'
import { api, ApiError } from '@/lib/api'
import { ehsRoutes } from '@/app/routes'
import { FormRenderer, looksLikeFailure, type FormValues, type Question } from '@/components/FormRenderer'
import { useMyProfile } from '@/hooks/useMyProfile'
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

  const flush = async () => {
    const batch = pending.current
    pending.current = {}
    if (Object.keys(batch).length === 0) return
    setSaveState('saving')
    try {
      await api.put(`/api/v1/forms/submissions/${id}/answers`, { values: batch })
      setSaveState('saved')
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

  useEffect(() => {
    if (loaded || answers.isLoading || !submission.data) return
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
      for (const q of submission.data.schema_snapshot ?? []) {
        if (q.type === 'date' && next[q.key] === undefined) {
          next[q.key] = today
          pending.current[q.key] = today
        }
      }
    }

    setValues(next)
    setLoaded(true)
  }, [loaded, answers.isLoading, answers.data, submission.data])

  // A form closed mid-edit still saves what was typed.
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  const submit = useMutation({
    mutationFn: async () => {
      if (timer.current) clearTimeout(timer.current)
      await flush()

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
      navigate('/forms/submissions')
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  if (submission.isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  if (submission.isError) {
    return <p className="p-4 text-sm text-danger-700">{(submission.error as ApiError).message}</p>
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
