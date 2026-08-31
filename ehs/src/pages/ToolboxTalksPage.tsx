/**
 * Toolbox talks: the month's topic, and collecting the room's signatures.
 *
 * The signing screen is the unusual part. Everyone who was at the meeting signs
 * on one device passed around afterwards — HSE's own records show seven
 * signatures inside two minutes from one location — so this is a list that grows
 * as the phone goes round, not a page each person visits.
 *
 * A supervisor sees only their own team's delivery; HSE sees the completion rate
 * across all of them, which is the number the management dashboard shows.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Plus, Users } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { SignaturePad } from '@/components/SignaturePad'
import { usePermissions } from '@/hooks/usePermissions'

interface Talk {
  id: string; talk_no: string; title: string; topic: string | null
  period_month: string; is_published: boolean
}
interface Delivery {
  id: string; talk_id: string; supervisor_name: string | null
  department_name: string | null; status: string; held_at: string | null
  expected_attendees: number; signed_count: number
}
interface Signer { user_id: string | null; person_name: string; person_role: string | null; signature: string }

const inputClass =
  'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
  'focus:border-primary-400 focus:outline-none'

export default function ToolboxTalksPage() {
  const { can } = usePermissions()
  const queryClient = useQueryClient()
  const isHse = can('ehs.settings.manage')
  const [signing, setSigning] = useState<Delivery | null>(null)
  const [creating, setCreating] = useState(false)

  const talks = useQuery({
    queryKey: ['toolbox-talks'],
    queryFn: () => api.get<Talk[]>('/api/v1/toolbox-talks'),
  })
  // A supervisor is shown what they have to deliver; HSE sees every team's.
  const deliveries = useQuery({
    queryKey: ['tbt-deliveries', isHse],
    queryFn: () => api.get<Delivery[]>(
      `/api/v1/toolbox-talks/deliveries${isHse ? '' : '?mine=true'}`),
  })

  const publish = useMutation({
    mutationFn: (talkId: string) => api.post(`/api/v1/toolbox-talks/${talkId}/publish`, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['toolbox-talks'] })
      queryClient.invalidateQueries({ queryKey: ['tbt-deliveries'] })
    },
  })

  const mine = (deliveries.data ?? []).filter((d) => d.status === 'pending')
  const done = (deliveries.data ?? []).filter((d) => d.status === 'delivered')

  return (
    <div className="p-4">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h1 className="text-lg font-bold tracking-tight text-neutral-900">Toolbox talks</h1>
        {isHse && (
          <button type="button" onClick={() => setCreating(true)}
                  className="flex min-h-[40px] items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-sm font-semibold text-white">
            <Plus className="h-4 w-4" aria-hidden />
            New topic
          </button>
        )}
      </div>

      {mine.length > 0 && (
        <>
          <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-neutral-400">
            To deliver
          </h2>
          <ul className="mb-6 flex flex-col gap-2">
            {mine.map((d) => (
              <li key={d.id}>
                <button type="button" onClick={() => setSigning(d)}
                        className="flex w-full items-center gap-3 rounded-xl border border-neutral-200 bg-white p-3.5 text-left transition-colors hover:border-primary-300">
                  <Users className="h-5 w-5 shrink-0 text-primary-600" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">
                      {talks.data?.find((t) => t.id === d.talk_id)?.title ?? 'Toolbox talk'}
                    </span>
                    <span className="mt-0.5 block text-xs text-neutral-500">
                      {d.department_name ?? d.supervisor_name}
                      {d.signed_count > 0 && ` · ${d.signed_count} signed so far`}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-neutral-400">
        Topics
      </h2>
      <ul className="flex flex-col gap-2">
        {talks.data?.map((t) => (
          <li key={t.id} className="rounded-xl border border-neutral-200 bg-white p-3.5">
            <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5">
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-neutral-900">{t.title}</span>
                <span className="mt-0.5 block text-xs text-neutral-500">
                  {t.talk_no} · {new Date(t.period_month + 'T00:00:00').toLocaleDateString(
                    undefined, { month: 'long', year: 'numeric' })}
                </span>
              </span>
              {t.is_published
                ? <Completion talkId={t.id} />
                : isHse && (
                  <button type="button" disabled={publish.isPending}
                          onClick={() => { if (!publish.isPending) publish.mutate(t.id) }}
                          className="min-h-[36px] rounded-lg bg-primary-600 px-3 text-xs font-semibold text-white disabled:opacity-50">
                    {publish.isPending ? 'Sending…' : 'Send to supervisors'}
                  </button>
                )}
            </div>
            {t.topic && <p className="mt-1.5 text-xs text-neutral-600">{t.topic}</p>}
          </li>
        ))}
      </ul>

      {done.length > 0 && (
        <>
          <h2 className="mb-2 mt-6 text-xs font-bold uppercase tracking-wider text-neutral-400">
            Delivered
          </h2>
          <ul className="flex flex-col gap-2">
            {done.map((d) => (
              <li key={d.id} className="flex items-center gap-2 rounded-xl border border-neutral-200 bg-white p-3 text-sm">
                <Check className="h-4 w-4 shrink-0 text-primary-600" aria-hidden />
                <span className="min-w-0 flex-1 text-neutral-800">
                  {d.department_name ?? d.supervisor_name}
                </span>
                <span className="text-xs text-neutral-500">
                  {d.signed_count} signed{d.held_at ? ` · ${formatDateTime(d.held_at)}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {signing && <SignRoomDialog delivery={signing} onClose={() => setSigning(null)}
                                  onDone={() => queryClient.invalidateQueries({ queryKey: ['tbt-deliveries'] })} />}
      {creating && <NewTopicDialog onClose={() => setCreating(false)}
                                    onDone={() => queryClient.invalidateQueries({ queryKey: ['toolbox-talks'] })} />}
    </div>
  )
}

function Completion({ talkId }: { talkId: string }) {
  const { data } = useQuery({
    queryKey: ['tbt-completion', talkId],
    queryFn: () => api.get<{ delivered: number; total: number; percent: number }>(
      `/api/v1/toolbox-talks/${talkId}/completion`),
  })
  if (!data) return null
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
      data.percent === 100 ? 'bg-primary-50 text-primary-700' : 'bg-warning-50 text-warning-700'}`}>
      {data.delivered}/{data.total} teams · {data.percent}%
    </span>
  )
}

/** The phone going round the room. */
function SignRoomDialog({ delivery, onClose, onDone }: {
  delivery: Delivery; onClose: () => void; onDone: () => void
}) {
  const [signers, setSigners] = useState<Signer[]>([])
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const [signature, setSignature] = useState<string | null>(null)
  const [expected, setExpected] = useState(String(delivery.expected_attendees || ''))
  const [error, setError] = useState<string | null>(null)

  const addSigner = () => {
    if (!person.name.trim() || !signature) return
    setSigners([...signers, {
      user_id: person.userId, person_name: person.name.trim(),
      person_role: null, signature,
    }])
    setPerson(EMPTY_PERSON)
    setSignature(null)
  }

  const finish = useMutation({
    mutationFn: () => api.post(`/api/v1/toolbox-talks/deliveries/${delivery.id}/sign`, {
      signatures: signers,
      held_at: new Date().toISOString(),
      expected_attendees: expected ? Number(expected) : null,
    }),
    onSuccess: () => { onDone(); onClose() },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
      <div className="max-h-[92vh] w-full overflow-y-auto rounded-t-2xl bg-white p-4 sm:max-w-md sm:rounded-2xl">
        <h2 className="text-base font-bold text-neutral-900">Who was at the talk?</h2>
        <p className="mt-0.5 text-xs text-neutral-500">
          Pass the phone round — add each person, then finish.
        </p>

        {signers.length > 0 && (
          <ul className="mb-3 mt-3 flex flex-col gap-1">
            {signers.map((s, i) => (
              <li key={i} className="flex items-center gap-2 rounded-lg bg-neutral-50 px-2.5 py-1.5 text-sm">
                <Check className="h-3.5 w-3.5 text-primary-600" aria-hidden />
                <span className="min-w-0 flex-1 truncate text-neutral-800">{s.person_name}</span>
                <button type="button" onClick={() => setSigners(signers.filter((_, j) => j !== i))}
                        className="text-xs text-neutral-400">Remove</button>
              </li>
            ))}
          </ul>
        )}

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Name</label>
        <PersonPicker value={person} onChange={setPerson} />

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Signature</label>
        <SignaturePad value={signature} onChange={setSignature}
                      label="Sign here" />

        <button type="button" onClick={addSigner}
                disabled={!person.name.trim() || !signature}
                className="mt-2 min-h-[40px] w-full rounded-lg border border-primary-300 bg-primary-50 text-sm font-medium text-primary-700 disabled:opacity-40">
          Add to the list
        </button>

        <label className="mb-1 mt-4 block text-sm font-medium text-neutral-800">
          How many were expected?
        </label>
        <input type="number" min={0} className={inputClass} value={expected}
               onChange={(e) => setExpected(e.target.value)} />

        {error && <p className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>}

        <div className="mt-4 flex gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 text-sm font-medium text-neutral-700">
            Cancel
          </button>
          <button type="button" disabled={signers.length === 0 || finish.isPending}
                  onClick={() => { if (signers.length && !finish.isPending) { setError(null); finish.mutate() } }}
                  className="min-h-[44px] flex-[1.4] rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50">
            {finish.isPending ? 'Saving…' : `Finish (${signers.length})`}
          </button>
        </div>
      </div>
    </div>
  )
}

function NewTopicDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [title, setTitle] = useState('')
  const [topic, setTopic] = useState('')
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7))
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/toolbox-talks', {
      title, topic: topic || null, period_month: `${month}-01`,
    }),
    onSuccess: () => { onDone(); onClose() },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
      <div className="w-full rounded-t-2xl bg-white p-4 sm:max-w-md sm:rounded-2xl">
        <h2 className="text-base font-bold text-neutral-900">New toolbox talk</h2>
        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Title</label>
        <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)}
               placeholder="Near miss reporting" />
        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Month</label>
        <input type="month" className={inputClass} value={month}
               onChange={(e) => setMonth(e.target.value)} />
        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Talking points</label>
        <textarea rows={4} className={`${inputClass} py-2`} value={topic}
                  onChange={(e) => setTopic(e.target.value)} />
        {error && <p className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>}
        <div className="mt-4 flex gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 text-sm font-medium text-neutral-700">
            Cancel
          </button>
          <button type="button" disabled={!title.trim() || create.isPending}
                  onClick={() => { if (title.trim() && !create.isPending) { setError(null); create.mutate() } }}
                  className="min-h-[44px] flex-[1.4] rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50">
            {create.isPending ? 'Creating…' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  )
}
