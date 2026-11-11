/**
 * Communications: every email Safety sent, for HSE.
 *
 * ★ Round 12, COM-01: "use this to log all email communications not just the
 * ones that are undelivered." They were always all filed against their record;
 * the page showed one kind. COM-03: searched on the person, the record's number
 * and the subject, by dates or this week / this month, with a button to send
 * one again as a reminder — a new message, listed on the record with its other
 * notifications. Q16: HSE only; a reminder goes to the people HSE picks from
 * those it went to; the words are removed after the retention days set under
 * Rules, and who was told, about what and when, stays.
 *
 * The list carries no words. Emails can hold injury details and what somebody
 * wrote in feedback, so each is opened on its own.
 *
 * ★ Review (uniops-8b), kept: a message that never left is sent again by hand,
 * never on a timer — once the mail server is fixed a timer would send a
 * fortnight of stale notices at once.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pagination, SortTh, sortParams, useSort } from '@uniops/shell'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import { SearchBox } from '@/components/ListView'
import TabLink from '@/components/TabLink'
import {
  ErrorNote, LoadFailedNote, PageHeader, primaryButton, secondaryButton,
} from '@/components/ui'

interface LogRow {
  id: string; created_at: string; sent_at: string | null; subject: string | null
  template_key: string | null; doc_type: string | null; doc_id: string | null
  doc_number: string | null; to_addrs: string[]; to_names: string[]
  error: string | null; resend_of: string | null; resent_by_name: string | null
  body_purged_at: string | null; path: string
}
interface LogPage { items: LogRow[]; total: number; retention_days: number }
interface Message { id: string; to_addrs: string[]; cc_addrs: string[]; body_text: string | null }

type SortKey = 'created_at' | 'subject' | 'doc_number' | 'sent_at'
type State = 'all' | 'sent' | 'unsent' | 'reminders'
type Period = 'any' | 'week' | 'month' | 'range'

const TH = 'px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500'
const STATES: { key: State; label: string }[] = [
  { key: 'all', label: 'Everything' },
  { key: 'sent', label: 'Sent' },
  { key: 'unsent', label: 'Not sent' },
  { key: 'reminders', label: 'Reminders' },
]

/** A plant-local day as YYYY-MM-DD — never through toISOString, which is UTC. */
function ymd(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function periodRange(p: Period): { from?: string; to?: string } {
  const today = new Date()
  if (p === 'week') {
    // The week starts on Monday, as the plant's rota does.
    const monday = new Date(today)
    monday.setDate(today.getDate() - ((today.getDay() + 6) % 7))
    return { from: ymd(monday), to: ymd(today) }
  }
  if (p === 'month') return { from: ymd(new Date(today.getFullYear(), today.getMonth(), 1)), to: ymd(today) }
  return {}
}

export default function UndeliveredEmailPage() {
  const [search, setSearch] = useState('')
  const [state, setState] = useState<State>('all')
  const [period, setPeriod] = useState<Period>('any')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [sort, onSort] = useSort<SortKey>(null, () => setPage(1))
  const [open, setOpen] = useState<string | null>(null)

  const range = period === 'range' ? { from: from || undefined, to: to || undefined } : periodRange(period)
  const log = useQuery({
    queryKey: ['communications', 'log', search.trim(), state, range.from, range.to, page, pageSize, sort],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize), state,
                                       ...sortParams(sort) })
      if (search.trim()) qs.set('q', search.trim())
      if (range.from) qs.set('from', range.from)
      if (range.to) qs.set('to', range.to)
      return api.get<LogPage>(`/api/v1/communications/log?${qs}`)
    },
    placeholderData: (prev) => prev,
  })
  const choosePeriod = (p: Period) => { setPeriod(p); setPage(1) }
  const chip = (on: boolean) =>
    `min-h-[36px] rounded-full px-3 text-xs font-medium ${on
      ? 'bg-primary-600 text-white' : 'bg-neutral-100 text-neutral-700 hover:bg-neutral-200'}`

  return (
    <div className="p-4">
      <PageHeader title="Communications"
                  subtitle="Every email Safety has sent, newest first. Open one to read it or send it again." />
      {log.data && (
        <p className="mb-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
          {log.data.retention_days > 0
            ? `What an email said is kept for ${log.data.retention_days} days (Settings → Rules); who it went to, about what and when is kept for good.`
            : 'What every email said is kept for good (Settings → Rules).'}
          {' '}A run of messages that were not sent usually means the mail server under Admin → Company Settings is missing or wrong.
        </p>
      )}

      <SearchBox value={search} onChange={(v) => { setSearch(v); setPage(1) }}
                 placeholder="Search a person's name or address, a record number, or the subject" />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {STATES.map((s) => (
          <button key={s.key} type="button" aria-pressed={state === s.key}
                  className={chip(state === s.key)}
                  onClick={() => { setState(s.key); setPage(1) }}>{s.label}</button>
        ))}
        <span className="mx-1 h-5 w-px bg-neutral-200" aria-hidden />
        {(['any', 'week', 'month', 'range'] as Period[]).map((p) => (
          <button key={p} type="button" aria-pressed={period === p} className={chip(period === p)}
                  onClick={() => choosePeriod(p)}>
            {p === 'any' ? 'Any time' : p === 'week' ? 'This week' : p === 'month' ? 'This month' : 'Between dates'}
          </button>
        ))}
        {period === 'range' && (
          <span className="flex flex-wrap items-center gap-1.5 text-xs text-neutral-700">
            <input type="date" aria-label="From" value={from} className="h-9 rounded-lg border border-neutral-300 px-2"
                   onChange={(e) => { setFrom(e.target.value); setPage(1) }} />
            to
            <input type="date" aria-label="To" value={to} className="h-9 rounded-lg border border-neutral-300 px-2"
                   onChange={(e) => { setTo(e.target.value); setPage(1) }} />
          </span>
        )}
      </div>

      {log.isError && <LoadFailedNote what="The communications" />}
      <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
        <div className="max-h-[calc(100vh-15rem)] overflow-auto">
          <table className="w-full min-w-[820px] text-sm">
            <thead className="sticky top-0 z-10 bg-neutral-50 shadow-[0_1px_0_#e5e5e5]">
              <tr>
                <SortTh label="Filed" sortKey="created_at" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Subject" sortKey="subject" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Record" sortKey="doc_number" sort={sort} onSort={onSort} className={TH} />
                <th className={TH}>To</th>
                <SortTh label="Sent" sortKey="sent_at" sort={sort} onSort={onSort} className={TH} />
              </tr>
            </thead>
            <tbody>
              {log.isPending && (
                <tr><td colSpan={5} className="p-6 text-center text-sm text-neutral-500">Loading…</td></tr>
              )}
              {log.data && log.data.items.length === 0 && (
                <tr>
                  <td colSpan={5} className="p-8 text-center text-sm text-neutral-500">
                    {search.trim() || state !== 'all' || period !== 'any'
                      ? 'Nothing matches.' : 'Safety has not sent anything yet.'}
                  </td>
                </tr>
              )}
              {log.data?.items.map((m) => (
                <Row key={m.id} m={m} open={open === m.id}
                     onToggle={() => setOpen(open === m.id ? null : m.id)} />
              ))}
            </tbody>
          </table>
        </div>
        <Pagination page={page} pageSize={pageSize} total={log.data?.total ?? 0} onPageChange={setPage}
                    onPageSizeChange={(n) => { setPageSize(n); setPage(1) }} />
      </div>
    </div>
  )
}

function Row({ m, open, onToggle }: { m: LogRow; open: boolean; onToggle: () => void }) {
  const who = m.to_names.length ? m.to_names.join(', ') : m.to_addrs.join(', ')
  return (
    <>
      <tr className="cursor-pointer border-t border-neutral-100 hover:bg-neutral-50" onClick={onToggle}>
        <td className="whitespace-nowrap px-3 py-2 text-xs tabular-nums text-neutral-600">{formatDateTime(m.created_at)}</td>
        <td className="px-3 py-2">
          <button type="button" aria-expanded={open} className="text-left font-medium text-neutral-900 hover:underline"
                  onClick={(e) => { e.stopPropagation(); onToggle() }}>
            {m.subject || '(no subject)'}
          </button>
          {m.resend_of && (
            <span className="ml-2 rounded-full bg-primary-50 px-2 py-0.5 text-[11px] font-semibold text-primary-700">
              Reminder{m.resent_by_name ? ` · ${m.resent_by_name}` : ''}
            </span>
          )}
        </td>
        <td className="whitespace-nowrap px-3 py-2 text-xs">
          {m.doc_number && m.path
            ? <TabLink to={m.path} onClick={(e) => e.stopPropagation()}
                       className="font-mono text-primary-700 hover:underline">{m.doc_number}</TabLink>
            : <span className="font-mono text-neutral-600">{m.doc_number ?? '—'}</span>}
        </td>
        <td className="max-w-[16rem] truncate px-3 py-2 text-xs text-neutral-700" title={m.to_addrs.join(', ')}>{who || '—'}</td>
        <td className="px-3 py-2 text-xs">
          {m.sent_at
            ? <span className="tabular-nums text-neutral-600">{formatDateTime(m.sent_at)}</span>
            : <span className="font-medium text-warning-700">Not sent{m.error ? ` — ${m.error}` : ''}</span>}
        </td>
      </tr>
      {open && (
        <tr className="bg-neutral-50/60">
          <td colSpan={5} className="px-3 py-3"><Opened row={m} /></td>
        </tr>
      )}
    </>
  )
}

/** One message's words, and sending it again. */
function Opened({ row }: { row: LogRow }) {
  const queryClient = useQueryClient()
  const message = useQuery({
    queryKey: ['communications', 'one', row.id],
    queryFn: () => api.get<Message>(`/api/v1/communications/log/${row.id}`),
  })
  const everyone = message.data ? [...message.data.to_addrs, ...message.data.cc_addrs] : []
  const [picked, setPicked] = useState<string[] | null>(null)
  const chosen = picked ?? everyone
  const done = () => {
    void queryClient.invalidateQueries({ queryKey: ['communications'] })
  }
  const remind = useMutation({
    mutationFn: () => api.post(`/api/v1/communications/${row.id}/remind`, { to: chosen }),
    onSuccess: done,
  })
  const sendAgain = useMutation({
    mutationFn: () => api.post<{ sent: boolean; message: string }>(
      `/api/v1/communications/${row.id}/send-again`),
    onSuccess: (r) => { if (r.sent) done() },
  })

  if (message.isPending) return <p className="text-xs text-neutral-500">Loading…</p>
  if (message.isError) return <ErrorNote error={message.error} />
  const purged = Boolean(row.body_purged_at)

  return (
    <div className="flex flex-col gap-3">
      {purged
        ? <p className="text-xs italic text-neutral-500">
            What it said was removed on {formatDateTime(row.body_purged_at)} after the retention period.
          </p>
        : <p className="max-w-[72ch] whitespace-pre-wrap text-sm text-neutral-800">{message.data.body_text}</p>}

      {!row.sent_at && !purged && (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={secondaryButton} disabled={sendAgain.isPending}
                  onClick={() => { if (!sendAgain.isPending) sendAgain.mutate() }}>
            {sendAgain.isPending ? 'Sending…' : 'Try sending it again'}
          </button>
          {sendAgain.data && !sendAgain.data.sent && (
            <span className="text-xs text-warning-700">{sendAgain.data.message}</span>
          )}
          <ErrorNote error={sendAgain.error} />
        </div>
      )}

      {/* COM-03 / Q16: a reminder, to the people HSE picks from those it went to. */}
      {row.sent_at && !purged && (
        <fieldset className="rounded-lg border border-neutral-200 bg-white p-3">
          <legend className="px-1 text-xs font-semibold text-neutral-700">Send it again as a reminder</legend>
          <ul className="flex flex-col gap-1">
            {everyone.map((a) => (
              <li key={a}>
                <label className="flex min-h-[32px] items-center gap-2 text-sm text-neutral-800">
                  <input type="checkbox" checked={chosen.includes(a)}
                         onChange={(e) => setPicked(e.target.checked
                           ? [...chosen, a] : chosen.filter((x) => x !== a))} />
                  {a}
                </label>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-neutral-500">
            Sent as a new message headed “Reminder”, and listed on the record with its other notifications.
          </p>
          <ErrorNote error={remind.error} />
          <div className="mt-2 flex items-center gap-3">
            <button type="button" className={primaryButton}
                    disabled={chosen.length === 0 || remind.isPending}
                    onClick={() => { if (chosen.length && !remind.isPending) remind.mutate() }}>
              {remind.isPending ? 'Sending…' : `Remind ${chosen.length === 1 ? '1 person' : `${chosen.length} people`}`}
            </button>
            {remind.isSuccess && <span className="text-xs text-success-700">Reminder filed and sent.</span>}
          </div>
        </fieldset>
      )}
    </div>
  )
}
