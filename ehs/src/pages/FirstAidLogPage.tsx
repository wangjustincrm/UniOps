/**
 * The first-aid register — Regulation 1101.
 *
 * A standing record of every treatment given, most of which never becomes an
 * incident. Entries are written once and never edited, so there is no edit
 * control here by design.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpRight, Plus } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'

interface Entry {
  id: string; log_no: string; incident_id: string | null; occurred_at: string
  location_path: string | null; injured_name: string; first_aider_name: string | null
  body_part_label: string | null; treatment_given: string; sent_offsite: boolean
}

export default function FirstAidLogPage() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canWrite = can('ehs.firstaid.write')
  const [escalating, setEscalating] = useState<{ id: string; log_no: string; injured_name: string } | null>(null)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({
    injured_name: '', body_part_label: '', treatment_given: '',
    date: new Date().toISOString().slice(0, 10),
    time: new Date().toTimeString().slice(0, 5),
    sent_offsite: false,
  })

  const { data, isLoading } = useQuery({
    queryKey: ['first-aid'],
    queryFn: () => api.get<Entry[]>('/api/v1/first-aid'),
  })

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/first-aid', {
      occurred_at: new Date(`${form.date}T${form.time}`).toISOString(),
      injured_name: form.injured_name,
      body_part_label: form.body_part_label || null,
      treatment_given: form.treatment_given,
      sent_offsite: form.sent_offsite,
    }),
    onSuccess: () => {
      setAdding(false)
      setForm({ ...form, injured_name: '', body_part_label: '', treatment_given: '' })
      queryClient.invalidateQueries({ queryKey: ['first-aid'] })
    },
  })

  const input = 'w-full min-h-[44px] rounded-lg border border-neutral-300 px-3 py-2 text-sm'

  return (
    <div className="p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-neutral-900">First-aid register</h1>
          <p className="text-xs text-neutral-500">
            Regulation 1101 — every treatment given, kept for at least five years.
          </p>
        </div>
        {can('ehs.firstaid.write') && (
          <button
            type="button"
            onClick={() => setAdding((v) => !v)}
            className="flex min-h-[44px] items-center gap-1.5 rounded-lg bg-primary-600 px-3.5 text-sm font-semibold text-white"
          >
            <Plus className="h-4 w-4" aria-hidden />
            Record
          </button>
        )}
      </div>

      {adding && (
        <form
          className="mb-4 rounded-xl border border-neutral-200 bg-white p-3.5"
          onSubmit={(e) => { e.preventDefault(); if (!create.isPending) create.mutate() }}
        >
          <div className="grid grid-cols-2 gap-2">
            <input type="date" className={input} value={form.date}
                   onChange={(e) => setForm({ ...form, date: e.target.value })} />
            <input type="time" className={input} value={form.time}
                   onChange={(e) => setForm({ ...form, time: e.target.value })} />
          </div>
          <input className={`${input} mt-2`} placeholder="Who was treated"
                 value={form.injured_name}
                 onChange={(e) => setForm({ ...form, injured_name: e.target.value })} />
          <input className={`${input} mt-2`} placeholder="Part of body (optional)"
                 value={form.body_part_label}
                 onChange={(e) => setForm({ ...form, body_part_label: e.target.value })} />
          <textarea className={`${input} mt-2`} rows={2} placeholder="Treatment given"
                    value={form.treatment_given}
                    onChange={(e) => setForm({ ...form, treatment_given: e.target.value })} />
          <label className="mt-2 flex min-h-[44px] items-center gap-2.5 text-sm text-neutral-700">
            <input type="checkbox" className="h-4 w-4" checked={form.sent_offsite}
                   onChange={(e) => setForm({ ...form, sent_offsite: e.target.checked })} />
            Sent off site for further treatment
          </label>
          {create.isError && (
            <p className="mt-1.5 text-xs text-danger-700">{(create.error as ApiError).message}</p>
          )}
          <button
            type="submit"
            disabled={create.isPending || !form.injured_name.trim() || !form.treatment_given.trim()}
            className="mt-2 min-h-[44px] w-full rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50"
          >
            {create.isPending ? 'Saving…' : 'Save entry'}
          </button>
        </form>
      )}

      {isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
      {data?.length === 0 && (
        <p className="p-8 text-center text-sm text-neutral-500">No treatments recorded yet.</p>
      )}
      <ul className="flex flex-col gap-2">
        {data?.map((e) => (
          <li key={e.id} className="rounded-xl border border-neutral-200 bg-white p-3.5">
            <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
              <span className="font-mono text-xs text-neutral-500">{e.log_no}</span>
              <span className="min-w-0 flex-1 text-sm font-semibold text-neutral-900">
                {e.injured_name}
                {e.body_part_label && (
                  <span className="ml-1.5 font-normal text-neutral-500">{e.body_part_label}</span>
                )}
              </span>
              {e.incident_id && (
                <span className="rounded-full bg-warning-50 px-2.5 py-0.5 text-xs font-semibold text-warning-700">
                  Escalated
                </span>
              )}
              {e.sent_offsite && (
                <span className="rounded-full bg-info-50 px-2.5 py-0.5 text-xs font-semibold text-info-700">
                  Sent off site
                </span>
              )}
            </div>
            <p className="mt-1.5 text-sm text-neutral-700">{e.treatment_given}</p>
            {/* The common path to a WSIB obligation: first aid at the time, a
                clinic two days later. Offered from the entry because that entry
                is usually the only contemporaneous record of when it happened. */}
            {!e.incident_id && canWrite && (
              <button
                type="button"
                onClick={() => setEscalating({ id: e.id, log_no: e.log_no, injured_name: e.injured_name })}
                className="mt-2 flex min-h-[36px] items-center gap-1.5 text-xs font-medium text-primary-700"
              >
                <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
                This became more than first aid
              </button>
            )}
            <p className="mt-1 text-xs text-neutral-400">
              {formatDateTime(e.occurred_at)}
              {e.location_path ? ` · ${e.location_path}` : ''}
              {e.first_aider_name ? ` · ${e.first_aider_name}` : ''}
            </p>
          </li>
        ))}
      </ul>

      {escalating && (
        <EscalateDialog entry={escalating} onClose={() => setEscalating(null)} />
      )}
    </div>
  )
}

/** Local "now" as a datetime-local value — the employer-aware instant defaults
 *  to when someone is doing this, which is usually right. */
function localNow(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
         `T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * Turning a first-aid entry into an incident.
 *
 * The usual path to a WSIB obligation: treatment that looked like first aid at
 * the time, and the worker goes to a clinic two days later. The register entry
 * is often the only contemporaneous record of when it happened.
 *
 * The date asked for here is deliberately NOT the treatment date. WSIB's three
 * business days run from when the employer learned there was an obligation, and
 * that is today, not the day of the cut. Getting these two confused starts the
 * clock in the past and makes a report look late that is not.
 */
function EscalateDialog({ entry, onClose }: {
  entry: { id: string; log_no: string; injured_name: string }
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [title, setTitle] = useState(`${entry.injured_name} — treatment became medical aid`)
  const [description, setDescription] = useState('')
  const [awareAt, setAwareAt] = useState(localNow())
  const [error, setError] = useState<string | null>(null)

  const escalate = useMutation({
    mutationFn: () => api.post<{ id: string; incident_no: string }>(
      `/api/v1/first-aid/${entry.id}/escalate`, {
        title,
        description: description || null,
        // Sent with the browser's offset so the clock starts from a real
        // instant rather than a floating local time.
        employer_aware_at: new Date(awareAt).toISOString(),
      }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['first-aid'] })
      onClose()
      navigate(`/incidents/${created.id}`)
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  const field = 'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
                'focus:border-primary-400 focus:outline-none'

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
      <div className="max-h-[92vh] w-full overflow-y-auto rounded-t-2xl bg-white p-4 sm:max-w-md sm:rounded-2xl">
        <h2 className="text-base font-bold text-neutral-900">Raise an incident</h2>
        <p className="mt-0.5 text-xs text-neutral-500">From {entry.log_no}</p>

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">What happened</label>
        <input className={field} value={title} onChange={(e) => setTitle(e.target.value)} />

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Detail</label>
        <textarea rows={3} className={`${field} py-2`} value={description}
                  onChange={(e) => setDescription(e.target.value)} />

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">
          When did we learn it was more than first aid?
        </label>
        <p className="mb-1.5 text-xs text-neutral-500">
          Not the date of the treatment. WSIB&rsquo;s three business days run from this
          moment, so putting the treatment date here starts the clock in the past.
        </p>
        <input type="datetime-local" className={field} value={awareAt}
               onChange={(e) => setAwareAt(e.target.value)} />

        {error && (
          <p className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>
        )}

        <div className="mt-4 flex gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 text-sm font-medium text-neutral-700">
            Cancel
          </button>
          <button type="button" disabled={!title.trim() || escalate.isPending}
                  onClick={() => { if (title.trim() && !escalate.isPending) { setError(null); escalate.mutate() } }}
                  className="min-h-[44px] flex-[1.4] rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50">
            {escalate.isPending ? 'Raising…' : 'Raise incident'}
          </button>
        </div>
      </div>
    </div>
  )
}
