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
import { useEffect, useState, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, FileText, Plus, Users, Paperclip } from 'lucide-react'
import { api, ApiError, openFile } from '@/lib/api'
import { todayForInput } from '@/lib/formatDate'
import { Pagination, SearchBox, useListView, DateRangeFilter } from '@/components/ListView'
import {
  PageHeader, SectionTitle, LoadFailedNote,
} from '@/components/ui'
import { anyFailed } from '@/lib/anyFailed'
import { formatDateTime } from '@/lib/formatDate'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { SignaturePad } from '@/components/SignaturePad'
import { usePermissions } from '@/hooks/usePermissions'
import ToolboxTracker from '@/components/ToolboxTracker'

interface Talk {
  id: string; talk_no: string; title: string; topic: string | null
  period_month: string; is_published: boolean; material_filename: string | null
}
interface Delivery {
  id: string; talk_id: string; supervisor_name: string | null
  department_name: string | null; status: string
  created_at: string; held_at: string | null
  expected_attendees: number; signed_count: number
  /** UAT 13: a talk held again names the delivery it repeats. */
  rerun_of?: string | null
  /** ★ Review R6: a talk being held again, not yet saved. Nothing exists on
   *  the server until the sheet is finished with signatures. */
  holdingAgain?: boolean
}
interface Signer { user_id: string | null; person_name: string; person_role: string | null; signature: string }

const SUPERVISORY = ['area_supervisor', 'dept_manager', 'supervisor']

const inputClass =
  'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
  'focus:border-primary-400 focus:outline-none'

export default function ToolboxTalksPage() {
  const { can, roles } = usePermissions()
  const queryClient = useQueryClient()
  const isHse = can('ehs.settings.manage')
  const [signing, setSigning] = useState<Delivery | null>(null)
  const [creating, setCreating] = useState(false)
  // ★ Arriving from My Actions, on the talk that was handed to me. The task
  // used to open this page at the top of the topic list and leave the
  // supervisor to find their own crew's row — an inbox that says "go and look"
  // is not an inbox.
  const [params, setParams] = useSearchParams()
  const openDelivery = params.get('delivery')

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

  const [sending, setSending] = useState<Talk | null>(null)
  // HSE could see a completion percentage and nothing behind it: not who the
  // talk went to, when it was handed over, who has signed or when. A number
  // with nobody's name beside it cannot be chased.
  const [showingState, setShowingState] = useState<string | null>(null)
  // Which delivery's signatures are open. On the page rather than inside the
  // HSE panel, because the person who collected them needs it too.
  const [openSigs, setOpenSigs] = useState<string | null>(null)

  // Opened once, when the delivery the task named has arrived, and the flag is
  // spent as it is used: these pages live in a keep-alive tab, so a remembered
  // "already opened this one" would stop the same supervisor opening the same
  // talk again next month.
  const named = openDelivery
    ? (deliveries.data ?? []).find((d) => d.id === openDelivery)
    : undefined
  useEffect(() => {
    if (!named) return
    setSigning(named)
    const next = new URLSearchParams(params)
    next.delete('delivery')
    setParams(next, { replace: true })
    // `params` and `setParams` change identity every render; the delivery that
    // arrived is what decides this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [named?.id])

  // ★ An empty "to deliver" list means the crews are up to date, and an empty
  // signature count means nobody signed. Neither is a failed request's to say.
  const partial = anyFailed(talks, deliveries)
  const mine = (deliveries.data ?? []).filter((d) => d.status === 'pending')
  const done = (deliveries.data ?? []).filter((d) => d.status === 'delivered')

  // Three lists, three searches. One box filtering all three would hide what
  // it was doing: a hit in Delivered while you are looking at what is still
  // owed reads as the filter having failed.
  const toDeliver = useListView(mine, (d) => [d.department_name, d.supervisor_name],
    { dateOf: (d) => d.held_at ?? d.created_at })
  const topics = useListView(talks.data, (t) => [t.talk_no, t.title, t.topic],
    { dateOf: (t) => t.period_month })
  const delivered = useListView(done, (d) => [d.department_name, d.supervisor_name],
    { dateOf: (d) => d.held_at ?? d.created_at })

  return (
    <div className="p-4">
      {partial && <LoadFailedNote what="Part of this page" />}
      <PageHeader title="Toolbox talks">
        {isHse && (
          <button type="button" onClick={() => setCreating(true)}
                  className="flex min-h-[40px] items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-sm font-semibold text-white">
            <Plus className="h-4 w-4" aria-hidden />
            New topic
          </button>
        )}
      </PageHeader>

      {/* ★ Round 12, TBT-01/02: HSE's month-by-month view, and the chase.
          UAT 13, TC-24: "place this tracker at the top of the page." */}
      {isHse && <ToolboxTracker />}

      {/* ★ UAT 13: "allow a supervisor the ability to pick a previous toolbox
          talk and redo the training session with staff … open up a new 'Who
          was at the talk' form." For whoever holds talks. */}
      {(isHse || roles.some((r) => SUPERVISORY.includes(r)) || (deliveries.data ?? []).length > 0) && (
        <HoldAgain talks={(talks.data ?? []).filter((t) => t.is_published)}
                   onStarted={(d) => {
                     void queryClient.invalidateQueries({ queryKey: ['tbt-deliveries'] })
                     setSigning(d)
                   }} />
      )}

      {mine.length > 0 && (
        <>
          <SectionTitle className="mb-2">To deliver</SectionTitle>
          <SearchBox value={toDeliver.query} onChange={toDeliver.setQuery}
                     placeholder="Search by crew or supervisor">
            <DateRangeFilter {...toDeliver.dateFilter} />
          </SearchBox>
          <ul className="mb-6 flex flex-col gap-2">
            {toDeliver.pageRows.map((d) => (
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
                      {d.rerun_of && ' · held again'}
                      {d.signed_count > 0 && ` · ${d.signed_count} signed so far`}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <Pagination page={toDeliver.page} pageCount={toDeliver.pageCount}
                      total={toDeliver.total} onPage={toDeliver.setPage} noun="to deliver" />
        </>
      )}

      <SectionTitle className="mb-2">Topics</SectionTitle>
      <SearchBox value={topics.query} onChange={topics.setQuery}
                 placeholder="Search topics by number, title or subject">
        <DateRangeFilter {...topics.dateFilter} />
      </SearchBox>
      <ul className="flex flex-col gap-2">
        {topics.pageRows.map((t) => (
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
                ? (
                  <span className="flex shrink-0 items-center gap-2">
                    <Completion talkId={t.id} />
                    {isHse && (
                      <button
                        type="button"
                        onClick={() => setShowingState(showingState === t.id ? null : t.id)}
                        aria-expanded={showingState === t.id}
                        className="min-h-[32px] rounded-md px-1.5 text-xs font-semibold text-neutral-500 hover:bg-neutral-50 hover:text-neutral-900"
                      >
                        Who has it
                      </button>
                    )}
                  </span>
                )
                : isHse && (
                  <button type="button" onClick={() => setSending(t)}
                          className="min-h-[36px] rounded-lg bg-primary-600 px-3 text-xs font-semibold text-white">
                    Send…
                  </button>
                )}
            </div>
            {t.topic && <p className="mt-1.5 text-xs text-neutral-600">{t.topic}</p>}
            {isHse && !t.is_published && <MaterialRow talk={t} />}
            {t.material_filename && t.is_published && (
              <p className="mt-1.5 text-xs text-neutral-500">
                {/* The crew signs against what this says, so everybody who is
                    asked to sign has to be able to read it — it was a filename
                    and nothing else. */}
                Sheet:{' '}
                <button type="button"
                        onClick={() => openFile(
                          `/api/v1/toolbox-talks/${t.id}/material`, t.material_filename ?? 'sheet')}
                        className="font-medium text-primary-700 hover:underline">
                  {t.material_filename}
                </button>
              </p>
            )}
            {showingState === t.id && <TalkState talkId={t.id} />}
          </li>
        ))}
      </ul>

      <Pagination page={topics.page} pageCount={topics.pageCount}
                  total={topics.total} onPage={topics.setPage} noun="topics" />

      {done.length > 0 && (
        <>
          <SectionTitle className="mb-2 mt-6">Delivered</SectionTitle>
          <SearchBox value={delivered.query} onChange={delivered.setQuery}
                     placeholder="Search delivered by crew or supervisor">
            <DateRangeFilter {...delivered.dateFilter} />
          </SearchBox>
          <ul className="flex flex-col gap-2">
            {delivered.pageRows.map((d) => (
              <li key={d.id} className="rounded-xl border border-neutral-200 bg-white p-3 text-sm">
                <div className="flex items-center gap-2">
                  <Check className="h-4 w-4 shrink-0 text-primary-600" aria-hidden />
                  <span className="min-w-0 flex-1 text-neutral-800">
                    {d.department_name ?? d.supervisor_name}
                  </span>
                  {/* Openable by whoever delivered it, not only by HSE. The
                      signature list was behind the HSE-only panel, so somebody
                      who had just collected the room's signatures saw the words
                      "1 signed" and nothing behind them — and read that as the
                      signatures not having been saved. They were. */}
                  {d.signed_count > 0 ? (
                    <button type="button"
                            onClick={() => setOpenSigs(openSigs === d.id ? null : d.id)}
                            aria-expanded={openSigs === d.id}
                            className="text-xs font-semibold text-primary-700 hover:underline">
                      {d.signed_count} signed
                    </button>
                  ) : (
                    <span className="text-xs text-neutral-500">Nobody signed</span>
                  )}
                  {d.held_at && (
                    <span className="text-xs text-neutral-500">{formatDateTime(d.held_at)}</span>
                  )}
                </div>
                {openSigs === d.id && <Signatures deliveryId={d.id} />}
              </li>
            ))}
          </ul>

          <Pagination page={delivered.page} pageCount={delivered.pageCount}
                      total={delivered.total} onPage={delivered.setPage} noun="deliveries" />
        </>
      )}

      {signing && <SignRoomDialog delivery={signing}
                    talk={talks.data?.find((t) => t.id === signing.talk_id)}
                    onClose={() => setSigning(null)}
                                  onDone={() => queryClient.invalidateQueries({ queryKey: ['tbt-deliveries'] })} />}
      {sending && <SendDialog talk={sending} onClose={() => setSending(null)} />}
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
    <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
      data.percent === 100 ? 'bg-primary-50 text-primary-700' : 'bg-warning-50 text-warning-700'}`}>
      {data.delivered}/{data.total} teams · {data.percent}%
    </span>
  )
}

/** The phone going round the room. */
function SignRoomDialog({ delivery, talk, onClose, onDone }: {
  delivery: Delivery
  /** The talk being delivered. The sheet is what the crew is signing against,
   *  so the person running the meeting has to be able to read it from here —
   *  it was only reachable from the topic list, which is not the screen they
   *  are on. */
  talk?: Talk
  onClose: () => void
  onDone: () => void
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
    mutationFn: () => api.post(delivery.holdingAgain
      ? `/api/v1/toolbox-talks/${delivery.talk_id}/hold-again`
      : `/api/v1/toolbox-talks/deliveries/${delivery.id}/sign`, {
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

        {talk && (
          <div className="mt-2.5 rounded-lg bg-neutral-50 px-3 py-2">
            <p className="text-sm font-semibold text-neutral-800">{talk.title}</p>
            {talk.topic && <p className="mt-0.5 text-xs text-neutral-600">{talk.topic}</p>}
            {talk.material_filename ? (
              <button type="button"
                      onClick={() => openFile(
                        `/api/v1/toolbox-talks/${talk.id}/material`,
                        talk.material_filename ?? 'sheet')}
                      className="mt-1.5 flex min-h-[32px] items-center gap-1.5 text-xs font-semibold text-primary-700 hover:underline">
                <FileText className="h-3.5 w-3.5" aria-hidden />
                Open the sheet
              </button>
            ) : (
              <p className="mt-1 text-xs text-neutral-500">No sheet was attached.</p>
            )}
          </div>
        )}

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
  // ★ The local month. `toISOString` would name the next one for anybody west
  // of UTC on the last evening of a month, which at UTC-4 is every month.
  const [month, setMonth] = useState(() => todayForInput().slice(0, 7))
  const [error, setError] = useState<string | null>(null)
  // ★ Chosen before the talk exists. The sheet could only be attached to a
  // saved talk, so writing one meant create, find it in the list, attach —
  // three steps for one thing, and the sheet *is* the talk.
  const [sheet, setSheet] = useState<File | null>(null)
  const sheetRef = useRef<HTMLInputElement>(null)

  const create = useMutation({
    mutationFn: async () => {
      const talk = await api.post<{ id: string }>('/api/v1/toolbox-talks', {
        title, topic: topic || null, period_month: `${month}-01`,
      })
      let sheetFailed = false
      if (sheet) {
        // The upload needs an id, so it follows rather than accompanies — and
        // ★ a failure here must not fail the whole thing. The talk already
        // exists by then; throwing would leave the form open with Create live,
        // and the next press would make a second talk with the same title and
        // send every supervisor two of them. The sheet can be attached from
        // the list, and this says so.
        const body = new FormData()
        body.append('file', sheet)
        try {
          await api.upload(`/api/v1/toolbox-talks/${talk.id}/material`, body)
        } catch {
          sheetFailed = true
        }
      }
      return { ...talk, sheetFailed }
    },
    onSuccess: (talk) => {
      if (talk.sheetFailed) {
        window.alert(
          'The talk was created, but the sheet did not upload. Open it in the '
          + 'list and attach the sheet before sending it out.')
      }
      onDone()
      onClose()
    },
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

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Sheet</label>
        <input ref={sheetRef} type="file" className="hidden"
               accept="application/pdf,image/png,image/jpeg,image/webp"
               onChange={(e) => setSheet(e.target.files?.[0] ?? null)} />
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => sheetRef.current?.click()}
                  className="flex min-h-[38px] items-center gap-1.5 rounded-lg border border-neutral-300 px-3 text-sm font-medium text-neutral-700">
            <Paperclip className="h-4 w-4" aria-hidden />
            {sheet ? 'Choose a different file' : 'Attach the sheet'}
          </button>
          {sheet && (
            <span className="min-w-0 flex-1 truncate text-xs text-neutral-600">{sheet.name}</span>
          )}
        </div>
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

/** Attaching the month's sheet. The topic *is* a one-page PDF. */
function MaterialRow({ talk }: { talk: Talk }) {
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const upload = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData()
      body.append('file', file)
      return api.upload(`/api/v1/toolbox-talks/${talk.id}/material`, body)
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['toolbox-talks'] }),
  })

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      <Paperclip className="h-3.5 w-3.5 text-neutral-400" aria-hidden />
      {talk.material_filename ? (
        <button type="button"
                onClick={() => openFile(
                  `/api/v1/toolbox-talks/${talk.id}/material`, talk.material_filename ?? 'sheet')}
                className="text-primary-700 hover:underline">
          {talk.material_filename}
        </button>
      ) : (
        <span className="text-neutral-600">
          No sheet attached — the crew signs against what it says
        </span>
      )}
      <input ref={inputRef} type="file" className="hidden"
             accept="application/pdf,image/png,image/jpeg,image/webp"
             onChange={(e) => {
               const file = e.target.files?.[0]
               if (file) upload.mutate(file)
               e.target.value = ''
             }} />
      <button type="button" disabled={upload.isPending}
              onClick={() => inputRef.current?.click()}
              className="min-h-[32px] rounded-md border border-neutral-200 px-2 font-medium text-neutral-700 disabled:opacity-40">
        {upload.isPending ? 'Uploading…' : talk.material_filename ? 'Replace' : 'Attach'}
      </button>
      {upload.isError && (
        <span className="text-danger-700">{(upload.error as ApiError).message}</span>
      )}
    </div>
  )
}

/**
 * Where one talk has got to, delivery by delivery.
 *
 * The completion percentage answered "how much of the plant", and HSE asked
 * for the half that lets them do something about it: which crews have it, how
 * long they have had it, and who in the room has actually put their name to it.
 */
function TalkState({ talkId }: { talkId: string }) {
  const deliveries = useQuery({
    queryKey: ['tbt-deliveries', 'talk', talkId],
    queryFn: () => api.get<Delivery[]>('/api/v1/toolbox-talks/deliveries'),
    select: (rows) => rows.filter((d) => d.talk_id === talkId),
  })
  const [openSigs, setOpenSigs] = useState<string | null>(null)

  if (!deliveries.data?.length) {
    return (
      <p className="mt-2 border-t border-neutral-100 pt-2 text-xs text-neutral-500">
        Not handed to anybody yet.
      </p>
    )
  }

  return (
    <ul className="mt-2 flex flex-col gap-1.5 border-t border-neutral-100 pt-2">
      {deliveries.data.map((d) => (
        <li key={d.id} className="text-xs">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="min-w-0 flex-1 text-neutral-800">
              {d.department_name ?? d.supervisor_name ?? 'Unassigned'}
            </span>
            <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
              d.status === 'delivered' ? 'bg-success-50 text-success-700'
                                       : 'bg-warning-50 text-warning-700'}`}>
              {d.status === 'delivered' ? 'Delivered' : 'Outstanding'}
            </span>
            <span className="text-neutral-500">
              assigned {formatDateTime(d.created_at)}
              {d.held_at && ` · held ${formatDateTime(d.held_at)}`}
            </span>
            {d.signed_count > 0 && (
              <button type="button"
                      onClick={() => setOpenSigs(openSigs === d.id ? null : d.id)}
                      aria-expanded={openSigs === d.id}
                      className="font-semibold text-primary-700 hover:underline">
                {d.signed_count} signed
              </button>
            )}
          </div>
          {openSigs === d.id && <Signatures deliveryId={d.id} />}
        </li>
      ))}
    </ul>
  )
}

function Signatures({ deliveryId }: { deliveryId: string }) {
  const sigs = useQuery({
    queryKey: ['tbt-signatures', deliveryId],
    queryFn: () => api.get<{ id: string; person_name: string; person_role: string | null
                             signed_at: string }[]>(
      `/api/v1/toolbox-talks/deliveries/${deliveryId}/signatures`),
  })
  if (!sigs.data?.length) return null
  return (
    <ul className="mt-1 flex flex-col gap-0.5 pl-3">
      {sigs.data.map((sig) => (
        <li key={sig.id} className="flex flex-wrap gap-x-2 text-neutral-600">
          <span className="text-neutral-800">{sig.person_name}</span>
          {sig.person_role && (
            <span className="text-neutral-400">{sig.person_role.replace(/_/g, ' ')}</span>
          )}
          <span>{formatDateTime(sig.signed_at)}</span>
        </li>
      ))}
    </ul>
  )
}

/**
 * Who this month's talk goes to.
 *
 * Everybody is the default and usually right — it is monthly and plant-wide —
 * but a topic that concerns two crews sent to forty people trains
 * thirty-eight of them to ignore the next one.
 */
function SendDialog({ talk, onClose }: { talk: Talk; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [chosen, setChosen] = useState<string[]>([])
  // A lead hand runs a crew and holds no supervisory role. The list offered
  // supervisors only, so there was no way to hand them the sheet.
  const [everyone, setEveryone] = useState(false)
  const [search, setSearch] = useState('')
  const supervisors = useQuery({
    queryKey: ['toolbox-supervisors', everyone],
    queryFn: () => api.get<{ id: string; full_name: string; role: string }[]>(
      `/api/v1/toolbox-talks/supervisors${everyone ? '?everyone=true' : ''}`),
  })
  const send = useMutation({
    mutationFn: () => api.post(`/api/v1/toolbox-talks/${talk.id}/publish`,
                               { supervisor_ids: chosen }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['toolbox-talks'] })
      queryClient.invalidateQueries({ queryKey: ['toolbox-deliveries'] })
      onClose()
    },
  })

  const loaded = supervisors.data ?? []
  // Ninety names is a scroll, not a list. Anybody already ticked stays visible
  // while searching, or narrowing the list would look like losing them.
  const all = search.trim()
    ? loaded.filter((s2) => chosen.includes(s2.id)
        || s2.full_name.toLowerCase().includes(search.trim().toLowerCase()))
    : loaded
  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4">
      <div role="dialog" aria-modal="true" aria-label="Send this talk"
           className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-t-2xl bg-white p-4 sm:rounded-2xl">
        <h2 className="text-sm font-semibold text-neutral-900">Send “{talk.title}”</h2>
        <p className="mt-1 text-xs text-neutral-500">
          Leave everyone unticked to send it to all supervisors, which is the
          usual thing to do.
        </p>

        <label className="mt-2 flex items-center gap-2 text-xs text-neutral-600">
          <input type="checkbox" checked={everyone}
                 onChange={(e) => { setEveryone(e.target.checked); setSearch('') }} />
          Show everybody, not only supervisors
        </label>

        {everyone && (
          <input
            className="mt-2 h-10 w-full rounded-lg border border-neutral-200 px-3 text-sm"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name"
            aria-label="Search staff by name"
          />
        )}
        <ul className="mt-3 flex flex-col gap-1">
          {all.map((s) => (
            <li key={s.id}>
              <label className="flex min-h-[36px] items-center gap-2 text-sm text-neutral-800">
                <input type="checkbox" checked={chosen.includes(s.id)}
                       onChange={(e) => setChosen(e.target.checked
                         ? [...chosen, s.id]
                         : chosen.filter((x) => x !== s.id))} />
                {s.full_name}
                <span className="text-xs text-neutral-400">{s.role.replace(/_/g, ' ')}</span>
              </label>
            </li>
          ))}
        </ul>
        {!talk.material_filename && (
          <p className="mt-3 rounded-lg bg-warning-50 p-2.5 text-xs text-warning-700">
            No sheet is attached. The crew signs against what the sheet says, so
            it is worth attaching one before this goes out.
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[40px] rounded-lg border border-neutral-200 px-3 text-sm">
            Cancel
          </button>
          <button type="button" disabled={send.isPending}
                  onClick={() => { if (!send.isPending) send.mutate() }}
                  className="min-h-[40px] rounded-lg bg-primary-600 px-3 text-sm font-semibold text-white disabled:opacity-50">
            {send.isPending ? 'Sending…'
              : chosen.length ? `Send to ${chosen.length}` : 'Send to everyone'}
          </button>
        </div>
      </div>
    </div>
  )
}


function HoldAgain({ talks, onStarted }: { talks: Talk[]; onStarted: (d: Delivery) => void }) {
  const [talkId, setTalkId] = useState('')
  // ★ Review R6: opening the sheet saves nothing. It was saved on opening, so
  // a sheet closed unsigned left a talk to deliver that nobody could remove,
  // chased every fortnight. Now the talk held again is saved, with its
  // signatures, when the sheet is finished.
  const open = () => {
    onStarted({
      id: '', talk_id: talkId, supervisor_name: null, department_name: null, status: 'pending',
      created_at: new Date().toISOString(), held_at: null, expected_attendees: 0, signed_count: 0,
      holdingAgain: true,
    })
    setTalkId('')
  }
  if (talks.length === 0) return null
  const sorted = [...talks].sort((a, b) => b.period_month.localeCompare(a.period_month))
  return (
    <section className="mb-6 rounded-xl border border-neutral-200 bg-white p-3.5" aria-label="Hold a past talk again">
      <SectionTitle className="mb-1">Hold a past talk again</SectionTitle>
      <p className="mb-2 text-xs text-neutral-500">
        Pick any talk already handed out and hold it with your crew again. It opens a new
        attendance sheet, and once signed is kept with the talk as held again.
      </p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <select className={`${inputClass} sm:flex-1`} value={talkId} aria-label="Which talk"
                onChange={(e) => setTalkId(e.target.value)}>
          <option value="">Choose a talk…</option>
          {sorted.map((t) => (
            <option key={t.id} value={t.id}>{t.talk_no} · {t.title} ({t.period_month.slice(0, 7)})</option>
          ))}
        </select>
        <button type="button" disabled={!talkId} onClick={() => { if (talkId) open() }}
                className="min-h-[44px] rounded-lg bg-primary-600 px-4 text-sm font-semibold text-white disabled:opacity-50">
          Hold it again
        </button>
      </div>
      <p className="mt-2 text-xs text-neutral-500">
        Nothing is saved until the sheet is finished with signatures; closed unsigned, it leaves nothing behind.
      </p>
    </section>
  )
}
