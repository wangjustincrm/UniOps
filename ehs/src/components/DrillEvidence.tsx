/**
 * The two things a drill evaluation leaves behind besides prose.
 *
 * Photographs, because "the north door was blocked" and a picture of the north
 * door blocked are not the same evidence, and it is the second one an
 * inspector asks for. And action items, because a drill that found a problem
 * and raised nothing is a drill that changed nothing — the finding sits in a
 * text box and the same door is blocked at the next one.
 *
 * The actions are ordinary corrective actions with the drill as their source,
 * so they arrive in the same inbox, chase on the same schedule and close under
 * the same verification as everything else. Nothing here is a second kind of
 * action item.
 */
import { Link } from 'react-router-dom'
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Paperclip, Plus, X } from 'lucide-react'
import { api, ApiError, openFile } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { ErrorNote, Field, inputClass, primaryButton, secondaryButton } from '@/components/ui'

interface Attachment {
  id: string; filename: string; content_type: string; file_size: number
  uploaded_by_name: string | null; created_at: string
}
interface Action {
  id: string; action_no: string; title: string; description: string | null; status: string
  owner_name: string | null; due_date: string
}

function inDays(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() + n)
  // Built from the local parts rather than toISOString, which would shift the
  // date by a day for anyone west of UTC — this plant is on Eastern time.
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${
    String(d.getDate()).padStart(2, '0')}`
}

export function DrillEvidence({ drillId, readOnly = false }: {
  drillId: string
  /** An evaluated drill is not edited — but it is still read, and that is
   *  precisely when somebody wants to see the photographs and what was
   *  raised. Read-only drops the controls, not the content. */
  readOnly?: boolean
}) {
  return (
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      <DrillPhotos drillId={drillId} readOnly={readOnly} />
      <DrillActions drillId={drillId} readOnly={readOnly} />
    </div>
  )
}

function DrillPhotos({ drillId, readOnly }: { drillId: string; readOnly: boolean }) {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)

  const photos = useQuery({
    queryKey: ['drill-attachments', drillId],
    queryFn: () => api.get<Attachment[]>(`/api/v1/drills/${drillId}/attachments`),
  })

  const upload = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData()
      body.append('file', file)
      return api.upload(`/api/v1/drills/${drillId}/attachments`, body)
    },
    onSuccess: () => {
      setError(null)
      queryClient.invalidateQueries({ queryKey: ['drill-attachments', drillId] })
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  const remove = useMutation({
    mutationFn: (id: string) =>
      api.del(`/api/v1/drills/${drillId}/attachments/${id}`),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['drill-attachments', drillId] }),
  })

  return (
    <div className="rounded-lg border border-neutral-200 p-2.5">
      <p className="mb-1.5 text-xs font-medium text-neutral-700">Photographs</p>

      <ul className="flex flex-col gap-1">
        {photos.data?.map((a) => (
          <li key={a.id} className="flex items-center gap-2 text-xs">
            <Paperclip className="h-3.5 w-3.5 shrink-0 text-neutral-400" aria-hidden />
            <button type="button"
                    onClick={() => openFile(
                      `/api/v1/drills/${drillId}/attachments/${a.id}/download`, a.filename)}
                    className="min-w-0 flex-1 truncate text-left text-primary-700 hover:underline">
              {a.filename}
            </button>
            <span className="shrink-0 text-neutral-400">
              {a.uploaded_by_name ?? '—'}, {formatDate(a.created_at)}
            </span>
            {!readOnly && <button type="button" aria-label={`Remove ${a.filename}`}
                    disabled={remove.isPending}
                    onClick={() => { if (!remove.isPending) remove.mutate(a.id) }}
                    className="shrink-0 rounded p-0.5 text-neutral-400 hover:bg-neutral-50">
              <X className="h-3.5 w-3.5" />
            </button>}
          </li>
        ))}
      </ul>
      {photos.data?.length === 0 && (
        <p className="text-xs text-neutral-500">
          Nothing attached. The muster point and anything that blocked a route
          are the two worth having.
        </p>
      )}

      {error && <p className="mt-1.5 text-xs text-danger-700">{error}</p>}

      <input
        ref={fileInput}
        type="file"
        // `capture` so a phone opens the camera rather than the gallery: these
        // are taken standing at the muster point, not found afterwards.
        accept="image/*,application/pdf"
        capture="environment"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) upload.mutate(file)
          e.target.value = ''
        }}
      />
      {!readOnly && (
        <button type="button" disabled={upload.isPending}
                onClick={() => fileInput.current?.click()}
                className="mt-1.5 text-xs font-semibold text-primary-700 hover:underline disabled:opacity-40">
          {upload.isPending ? 'Uploading…' : 'Add a photograph'}
        </button>
      )}
    </div>
  )
}

function DrillActions({ drillId, readOnly }: { drillId: string; readOnly: boolean }) {
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [title, setTitle] = useState('')
  const [owner, setOwner] = useState<PersonValue>(EMPTY_PERSON)
  const [dueDate, setDueDate] = useState(inDays(14))

  const actions = useQuery({
    queryKey: ['actions', 'drill', drillId],
    queryFn: () => api.get<Action[]>(
      `/api/v1/actions?source_type=drill&source_id=${drillId}`),
  })

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/actions', {
      source_type: 'drill',
      source_id: drillId,
      title,
      owner_id: owner.userId,
      due_date: dueDate,
    }),
    onSuccess: () => {
      setTitle(''); setOwner(EMPTY_PERSON); setAdding(false)
      queryClient.invalidateQueries({ queryKey: ['actions', 'drill', drillId] })
    },
  })

  return (
    <div className="rounded-lg border border-neutral-200 p-2.5">
      <p className="mb-1.5 text-xs font-medium text-neutral-700">Action items</p>

      {/* ★ Round 4, TC-14: "any root cause analysis, or capa completed as part
          of the drill or on any form shall display the capa description, and the
          information in the history of changes."

          The row had a number, a title, an owner and a date, and was not a
          link — so the description and the trail were both a search away. A
          title is a label; the description is the instruction, and it is what
          somebody reading a root cause needs to judge whether the cause has
          been answered. */}
      <ul className="flex flex-col gap-1.5">
        {actions.data?.map((a) => (
          <li key={a.id}>
            <Link to={`/actions/${a.id}`}
                  className="block rounded-md px-1.5 py-1 text-xs hover:bg-primary-50/50">
              <span className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-mono text-neutral-500">{a.action_no}</span>
                <span className="min-w-0 flex-1 font-medium text-neutral-800">{a.title}</span>
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold capitalize ${
                  a.status === 'closed' || a.status === 'verified'
                    ? 'bg-success-50 text-success-700'
                    : a.status === 'in_progress'
                      ? 'bg-warning-50 text-warning-700'
                      : 'bg-neutral-100 text-neutral-600'}`}>
                  {(a.status ?? 'open').replace('_', ' ')}
                </span>
              </span>
              {a.description && (
                <span className="mt-0.5 block whitespace-pre-wrap text-neutral-600">
                  {a.description}
                </span>
              )}
              <span className="mt-0.5 block text-[11px] text-neutral-400">
                {a.owner_name ?? 'Unassigned'} · due {formatDate(a.due_date)} ·
                {' '}open it for who changed what, and when
              </span>
            </Link>
          </li>
        ))}
      </ul>
      {actions.data?.length === 0 && !adding && (
        <p className="text-xs text-neutral-500">
          Nothing raised. A drill that found a problem and raised nothing is a
          drill that changed nothing.
        </p>
      )}

      {adding ? (
        <div className="mt-2 flex flex-col gap-2">
          <Field label="What needs doing">
            <input className={inputClass} value={title}
                   onChange={(e) => setTitle(e.target.value)}
                   placeholder="Clear the pallets from the north fire exit" />
          </Field>
          <Field label="Who">
            <PersonPicker value={owner} onChange={setOwner} allowExternal={false} />
          </Field>
          <Field label="By when">
            <input type="date" className={inputClass} value={dueDate}
                   onChange={(e) => setDueDate(e.target.value)} />
          </Field>
          <ErrorNote error={create.error} />
          <div className="flex justify-end gap-2">
            <button type="button" className={secondaryButton}
                    onClick={() => setAdding(false)}>Cancel</button>
            <button type="button" className={primaryButton}
                    disabled={!title.trim() || !owner.userId || create.isPending}
                    onClick={() => { if (!create.isPending) create.mutate() }}>
              {create.isPending ? 'Raising…' : 'Raise it'}
            </button>
          </div>
        </div>
      ) : !readOnly && (
        <button type="button" onClick={() => setAdding(true)}
                className="mt-1.5 inline-flex items-center gap-1 text-xs font-semibold text-primary-700 hover:underline">
          <Plus className="h-3.5 w-3.5" aria-hidden />
          Raise an action
        </button>
      )}
    </div>
  )
}
