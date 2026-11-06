/**
 * HSE news: the list everybody sees at the top of My Actions, and the box
 * HSE writes it in.
 *
 * Round 10 (open box): "Create a news section on the my action register for
 * the hse role. on this page create a large text box where the hse role can
 * input news items that are important for staff to see and know. the layout
 * should be as follows: Date, Time, Importance rating (high, medium, low), and
 * Message. the importance should be a button shape and Colour code each
 * importance based on the rating. High is red, medium is orange, and low is
 * yellow. for all staff create a news ticker on the my actions dashboard at
 * the top of the page that will get information from the HSE role news item in
 * the same layout." The message is shown as text, never as markup.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Megaphone } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { ErrorNote, inputClass, primaryButton, secondaryButton } from '@/components/ui'

export interface NewsItem {
  id: string; posted_at: string; importance: 'high' | 'medium' | 'low'
  message: string; posted_by_name: string | null
}

const LEVELS = [
  { key: 'high', label: 'High', cls: 'bg-danger-600 text-white', idle: 'border-danger-300 text-danger-700' },
  { key: 'medium', label: 'Medium', cls: 'bg-orange-500 text-white', idle: 'border-orange-300 text-orange-700' },
  { key: 'low', label: 'Low', cls: 'bg-yellow-300 text-yellow-950', idle: 'border-yellow-300 text-yellow-800' },
] as const

const KEY = ['news']

export function useNews() {
  return useQuery({ queryKey: KEY, queryFn: () => api.get<NewsItem[]>('/api/v1/news') })
}

function Importance({ level }: { level: NewsItem['importance'] }) {
  const l = LEVELS.find((x) => x.key === level) ?? LEVELS[2]
  return (
    <span className={`inline-flex shrink-0 items-center rounded-full px-2.5 py-0.5 text-[11px] font-bold uppercase tracking-wide ${l.cls}`}>
      {l.label}
    </span>
  )
}

function when(at: string) {
  const d = new Date(at)
  return `${formatDate(at)} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

function Item({ n }: { n: NewsItem }) {
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
      <span className="tabular-nums text-neutral-500">{when(n.posted_at)}</span>
      <Importance level={n.importance} />
      <span className="min-w-0 flex-1 text-neutral-900">{n.message}</span>
    </li>
  )
}

/** For all staff, at the top of My Actions. Nothing when there is no news.
 *
 *  ★ Round 11, TC-17: "place those messages under each other rather then beside
 *  each other." It was a scrolling ticker; a list also reads on a phone. */
export function NewsList() {
  const news = useNews()
  const items = news.data ?? []
  if (!items.length) return null
  return (
    <section aria-label="HSE news"
             className="mb-1 flex items-start gap-3 rounded-xl border border-neutral-200 bg-white px-3 py-2">
      <Megaphone className="mt-0.5 h-4 w-4 shrink-0 text-primary-600" aria-hidden />
      <ul className="flex max-h-40 min-w-0 flex-1 flex-col gap-1.5 overflow-y-auto">
        {items.map((n) => <Item key={n.id} n={n} />)}
      </ul>
    </section>
  )
}

/** HSE's own box: write an item, see what is up, take one down. */
export function NewsDesk() {
  const queryClient = useQueryClient()
  const news = useNews()
  const [importance, setImportance] = useState<NewsItem['importance'] | null>(null)
  const [message, setMessage] = useState('')
  const post = useMutation({
    mutationFn: () => api.post('/api/v1/news', { importance, message: message.trim() }),
    onSuccess: () => { setMessage(''); setImportance(null); queryClient.invalidateQueries({ queryKey: KEY }) },
  })
  const takeDown = useMutation({
    mutationFn: (id: string) => api.post(`/api/v1/news/${id}/archive`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: KEY }),
  })
  return (
    <section className="mb-1 rounded-xl border border-neutral-200 bg-white p-3.5" aria-label="News for staff">
      <p className="mb-2 text-sm font-semibold text-neutral-900">News for staff</p>
      <textarea rows={4} className={`${inputClass} py-2`} value={message} maxLength={2000}
                aria-label="News message" placeholder="What staff need to see and know"
                onChange={(e) => setMessage(e.target.value)} />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="text-xs text-neutral-600">Importance</span>
        {LEVELS.map((l) => (
          <button key={l.key} type="button" aria-pressed={importance === l.key}
                  onClick={() => setImportance(l.key)}
                  className={`min-h-[32px] rounded-full border px-3 text-xs font-bold uppercase tracking-wide ${
                    importance === l.key ? `${l.cls} border-transparent` : `bg-white ${l.idle}`}`}>
            {l.label}
          </button>
        ))}
        <span className="flex-1" />
        <button type="button" className={primaryButton}
                disabled={!importance || !message.trim() || post.isPending}
                onClick={() => { if (!post.isPending) post.mutate() }}>
          {post.isPending ? 'Posting…' : 'Post it'}
        </button>
      </div>
      <ErrorNote error={post.error ?? takeDown.error} />
      {news.isError && <p className="mt-2 text-xs text-warning-700">The news could not be loaded.</p>}
      {(news.data?.length ?? 0) > 0 && (
        <ul className="mt-3 flex flex-col gap-1.5 border-t border-neutral-100 pt-3">
          {news.data!.map((n) => (
            <li key={n.id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="tabular-nums text-xs text-neutral-500">{when(n.posted_at)}</span>
              <Importance level={n.importance} />
              <span className="min-w-0 flex-1 text-neutral-900">{n.message}</span>
              <button type="button" className={secondaryButton} disabled={takeDown.isPending}
                      onClick={() => { if (!takeDown.isPending) takeDown.mutate(n.id) }}>
                Take down
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
