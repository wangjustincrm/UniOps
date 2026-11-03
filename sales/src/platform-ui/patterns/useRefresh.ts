import { useContext, useEffect, useRef, useState } from 'react'
import { hashKey, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { RecordSheetHostContext } from './recordSheetHost'
import { changed, describeDiff, describeRecordChange, diffRows, type Key } from './refreshDiff'

// THE one way to refresh what a user sees (the user, 2026-11-03: one reusable
// refresh). A click, an auto-refresh tick, a record sheet's refresh, and the
// incidental fetches (the window focus, an action's invalidation) all end in
// the same place: every arrival of the query's data is compared with the last
// one here, and the result is one message, one highlight and one busy state.
// A click and a tick look the same (the bar and the spin at once, ≥ 600 ms,
// then "Up to date: no changes" or "1 new · 2 updated"); an incidental fetch
// speaks only when something changed. Features never call refetch() themselves
// (ESLint).

export interface RefreshMessage { text: string; tone: 'info' | 'muted' | 'danger' }

/** What a refresh found: words (null: nothing changed) and the rows to highlight. */
export interface Comparison { text: string | null; highlight?: Key[] }

export interface RefreshState {
  /** The user's Refresh (and Try again). */
  refresh: () => Promise<void>
  /** A visible refresh is running (a click or a tick): the bar and the spin, ≥ 600 ms. */
  busy: boolean
  /** Any fetch is running (incidental ones too: the bar after 200 ms). */
  fetching: boolean
  message: RefreshMessage | null
  highlight: ReadonlySet<Key>
  updatedAt: number
  /** Auto-refresh, in seconds (0: off). */
  auto: number
  /** Present where the user chooses the interval (a list); a record sheet follows its list. */
  setAuto?: (seconds: number) => void
  /** Counts the auto-refresh ticks (a list gives it to its open record's sheet). */
  tick: number
}

interface QueryLike<D> {
  data: D | undefined
  dataUpdatedAt: number
  isFetching: boolean
  refetch: () => Promise<{ isError: boolean }>
}

export interface RefreshOptions<D> {
  /** The query's key: its arrivals are watched in the cache. */
  queryKey: QueryKey
  /** How two arrivals differ (compareRows for a list page, compareRecord for one record). */
  compare: (before: D, after: D) => Comparison
  /** What a visible refresh says when nothing changed. */
  noChange?: string
  /** A number: auto-refresh every so many seconds (0 off), with setAuto. 'follow': tick with the list
   *  that opened this record (its RecordSheetHost). */
  auto?: number | 'follow'
  setAuto?: (seconds: number) => void
}

const MIN_BUSY = 600
const SHOWN = 3000
const LIT = 2000

/** A list page's rows ({ items, total }), by key; the total tells new rows beyond the page. */
export const compareRows = <R,>(key: (row: R) => Key) =>
  (before: { items: R[]; total: number }, after: { items: R[]; total: number }): Comparison => {
    const d = diffRows(before.items, after.items, key, { before: before.total, after: after.total })
    return { text: describeDiff(d), highlight: changed(d) ? [...d.added, ...d.updated] : [] }
  }

/** One record: the top-level fields that changed ("Updated: status, lines"). */
export const compareRecord = (before: unknown, after: unknown): Comparison => {
  const what = describeRecordChange(before as Record<string, unknown>, after as Record<string, unknown>, {}, {})
  return { text: what ? `Updated: ${what}` : null }
}

export function useRefresh<D>(query: QueryLike<D>, opts: RefreshOptions<D>): RefreshState {
  const qc = useQueryClient()
  const host = useContext(RecordSheetHostContext)
  const hash = hashKey(opts.queryKey)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<RefreshMessage | null>(null)
  const [highlight, setHighlight] = useState<ReadonlySet<Key>>(new Set())
  const [tick, setTick] = useState(0)
  const asking = useRef<'visible' | null>(null)
  const last = useRef<{ hash: string; data: D } | null>(null)
  const timers = useRef<{ msg?: ReturnType<typeof setTimeout>; lit?: ReturnType<typeof setTimeout> }>({})
  const latest = useRef(opts)
  useEffect(() => { latest.current = opts })
  useEffect(() => () => { clearTimeout(timers.current.msg); clearTimeout(timers.current.lit) }, [])

  const say = (text: string, tone: RefreshMessage['tone']) => {
    setMessage({ text, tone })
    clearTimeout(timers.current.msg)
    timers.current.msg = setTimeout(() => setMessage(null), SHOWN)
  }
  const sayRef = useRef(say)
  useEffect(() => { sayRef.current = say })

  // Every arrival of this query's data, whoever asked: compared with the last one.
  useEffect(() => {
    const cached = qc.getQueryCache().get(hash)?.state.data as D | undefined
    last.current = cached === undefined ? null : { hash, data: cached }  // a query seen before: its data is the baseline
    return qc.getQueryCache().subscribe((e) => {
      if (e.query.queryHash !== hash || e.type !== 'updated' || e.action.type !== 'success') return
      const data = e.query.state.data as D
      const prev = last.current
      last.current = { hash, data }
      const visible = asking.current === 'visible'
      asking.current = null
      if (!prev || prev.hash !== hash) return  // the first page of a query: nothing to compare
      const c = latest.current.compare(prev.data, data)
      if (visible) sayRef.current(c.text ?? latest.current.noChange ?? 'Up to date: no changes', c.text ? 'info' : 'muted')
      else if (c.text) sayRef.current(c.text, 'info')  // incidental: only when something changed
      if (c.highlight?.length) {
        setHighlight(new Set(c.highlight))
        clearTimeout(timers.current.lit)
        timers.current.lit = setTimeout(() => setHighlight(new Set()), LIT)
      }
    })
  }, [qc, hash])

  // A visible refresh: a click or a tick. The bar and the spin at once, for ≥ 600 ms.
  const refresh = async () => {
    asking.current = 'visible'
    setBusy(true)
    const started = Date.now()
    try {
      const r = await query.refetch()
      if (r.isError) { asking.current = null; say('Refresh failed: try again', 'danger') }
    } finally {
      await new Promise((ok) => setTimeout(ok, Math.max(0, MIN_BUSY - (Date.now() - started))))
      setBusy(false)
    }
  }
  const refreshRef = useRef(refresh)
  useEffect(() => { refreshRef.current = refresh })

  // Auto-refresh of its own: a tick every N seconds while the tab is shown;
  // hidden, it waits; shown again, it ticks at once (TanStack's own interval is
  // not used for this: refetchIntervalInBackground would be false anyway).
  const seconds = typeof opts.auto === 'number' ? opts.auto : 0
  const [hidden, setHidden] = useState(() => typeof document !== 'undefined' && document.visibilityState === 'hidden')
  useEffect(() => {
    const onVis = () => setHidden(document.visibilityState === 'hidden')
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [])
  const wasHidden = useRef(false)
  useEffect(() => {
    if (!seconds) return
    if (hidden) { wasHidden.current = true; return }
    const back = wasHidden.current
    wasHidden.current = false
    let t: ReturnType<typeof setTimeout> | undefined
    const plan = (delay: number) => {
      t = setTimeout(async () => {
        setTick((n) => n + 1)
        await refreshRef.current()
        plan(seconds * 1000)
      }, delay)
    }
    plan(back ? 0 : seconds * 1000)
    return () => clearTimeout(t)
  }, [seconds, hidden])

  // A record sheet follows the list that opened it: the same tick, the same refresh.
  const follow = opts.auto === 'follow' ? (host?.autoTick ?? 0) : 0
  useEffect(() => {
    if (!follow) return
    const t = setTimeout(() => { void refreshRef.current() }, 0)
    return () => clearTimeout(t)
  }, [follow])

  return {
    refresh, busy, fetching: query.isFetching, message, highlight, updatedAt: query.dataUpdatedAt,
    auto: opts.auto === 'follow' ? (host?.auto ?? 0) : seconds, setAuto: opts.setAuto, tick,
  }
}
