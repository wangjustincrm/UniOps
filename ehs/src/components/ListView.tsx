/**
 * The search box and the pager that every list in this module needs.
 *
 * One mechanism rather than twelve: the registers here are the same shape —
 * a few hundred rows the browser already holds — and twelve hand-rolled
 * search boxes is how the module ended up with four spellings of a status
 * pill.
 *
 * ★ Filtering happens in the browser, on rows already fetched. That is honest
 * at this plant's scale — three hundred people file on the order of three
 * hundred incidents a year, so the largest of these lists reaches a few
 * thousand rows over the life of the system, which is a few hundred kilobytes
 * of JSON. It stops being honest the moment the server sends back less than
 * everything, which is why `capped` exists: a pager that says "1–20 of 50"
 * while the server quietly held back the other four thousand is worse than no
 * pager at all.
 */
import { useMemo, useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { dayOf, localDay, todayForInput } from '@/lib/formatDate'
import { inputSized } from './ui'

export const PAGE_SIZE = 20

/** What a row contributes to the search. Null and undefined are skipped. */
export type Searchable<T> = (row: T) => (string | null | undefined)[]

/**
 * The date ranges the registers offer.
 *
 * Every preset is a range of whole calendar days, because that is what the
 * records carry — an inspection is dated, not timestamped to the minute — and
 * because "the last 24 hours" invites an argument about what happened 25 hours
 * ago that "Today" does not. Anything the presets do not cover is what Custom
 * is for, and an audit pack is always a custom range.
 */
export type RangePreset =
  | 'all' | 'today' | 'last7' | 'last30' | 'this_month' | 'last_month' | 'custom'

export const RANGE_LABELS: { value: RangePreset; label: string }[] = [
  { value: 'all', label: 'All dates' },
  { value: 'today', label: 'Today' },
  { value: 'last7', label: 'Last 7 days' },
  { value: 'last30', label: 'Last 30 days' },
  { value: 'this_month', label: 'This month' },
  { value: 'last_month', label: 'Last month' },
  { value: 'custom', label: 'Custom range…' },
]

/** Inclusive `yyyy-mm-dd` bounds; a null end is open. */
export interface DayRange { from: string | null; to: string | null }

function boundsFor(preset: RangePreset, from: string, to: string): DayRange {
  const now = new Date()
  const shifted = (days: number) => {
    const d = new Date(now)
    d.setDate(d.getDate() - days)
    return dayOf(d)
  }
  switch (preset) {
    case 'today': return { from: dayOf(now), to: dayOf(now) }
    // Inclusive of today, so "last 7 days" is a week's worth of rows and not
    // eight days of them.
    case 'last7': return { from: shifted(6), to: dayOf(now) }
    case 'last30': return { from: shifted(29), to: dayOf(now) }
    case 'this_month':
      return { from: dayOf(new Date(now.getFullYear(), now.getMonth(), 1)), to: dayOf(now) }
    case 'last_month':
      return {
        from: dayOf(new Date(now.getFullYear(), now.getMonth() - 1, 1)),
        // Day 0 of this month is the last day of the previous one.
        to: dayOf(new Date(now.getFullYear(), now.getMonth(), 0)),
      }
    case 'custom': return { from: from || null, to: to || null }
    default: return { from: null, to: null }
  }
}

export interface DateFilter {
  preset: RangePreset
  from: string
  to: string
  setPreset: (p: RangePreset) => void
  setFrom: (v: string) => void
  setTo: (v: string) => void
  range: DayRange
  /** What the filter is currently doing, for the empty state to explain. */
  label: string
}

/**
 * The range state on its own, for a list whose filtering happens on the server.
 *
 * `useListView` calls this for the lists it filters in the browser. A list that
 * asks the server instead reads `range.from` / `range.to` into its query — same
 * control, same presets, same words on screen, one implementation.
 */
export function useDateFilter(
  onChange?: () => void,
  /** Where the filter starts — a link that already names a range, typically a
   *  figure opened from the dashboard. Read once, then the control owns it. */
  initial?: { from?: string | null; to?: string | null },
): DateFilter {
  const [preset, setPresetState] = useState<RangePreset>(
    initial?.from || initial?.to ? 'custom' : 'all')
  const [from, setFromState] = useState(initial?.from ?? '')
  const [to, setToState] = useState(initial?.to ?? '')

  // ★ The range follows `initial` whenever `initial` changes, not only on the
  // first render. These pages live in a keep-alive tab keyed by path, so
  // arriving a second time with a different range in the URL does not remount
  // anything: read once, the control would keep June while the rest of the
  // page had moved on to the whole year, and the list would be the overlap of
  // two filters the reader never asked for together.
  const wanted = `${initial?.from ?? ''}|${initial?.to ?? ''}`
  const [lastWanted, setLastWanted] = useState(wanted)
  if (wanted !== lastWanted) {
    setLastWanted(wanted)
    setPresetState(initial?.from || initial?.to ? 'custom' : 'all')
    setFromState(initial?.from ?? '')
    setToState(initial?.to ?? '')
  }

  // `today` is a dependency, not decoration: these tabs stay mounted for days,
  // and a "Today" filter still showing yesterday's rows after midnight is the
  // sort of thing nobody thinks to check.
  const today = todayForInput()
  const range = useMemo(() => boundsFor(preset, from, to), [preset, from, to, today])

  return {
    preset,
    from,
    to,
    // Narrowing sends you back to the first page; staying on page four of a
    // result that now has one page is the pager's job to clamp, not a place
    // anyone meant to be.
    setPreset: (p) => { setPresetState(p); onChange?.() },
    setFrom: (v) => { setFromState(v); onChange?.() },
    setTo: (v) => { setToState(v); onChange?.() },
    range,
    label: RANGE_LABELS.find((r) => r.value === preset)?.label ?? 'All dates',
  }
}

export interface ListViewOptions<T> {
  /**
   * Which date on a row the range filter applies to. Pass it and the list gets
   * a range filter; leave it out and it does not. Choose the date the register
   * is *about* — when the incident happened, when the talk was delivered — not
   * when the row was created, which is the one nobody goes looking for.
   */
  dateOf?: (row: T) => string | null | undefined
  /**
   * A value that means "these are different rows now" — the date range a list
   * sends to the server, typically. When it changes the pager goes back to the
   * first page, which is what the in-browser filters do for themselves.
   */
  resetKey?: string
  /**
   * What the search box starts with — a link that arrives already narrowed to
   * one record, typically from an action naming the hazard or drill it was
   * raised from. Followed whenever it changes, not only on the first render,
   * because these pages live in a keep-alive tab and arriving a second time
   * does not remount them.
   */
  query?: string
}

export function useListView<T>(
  rows: T[] | undefined,
  searchable: Searchable<T>,
  options: ListViewOptions<T> = {},
) {
  const { dateOf, resetKey, query: wantedQuery = '' } = options
  const [query, setQuery] = useState(wantedQuery)
  const [lastQuery, setLastQuery] = useState(wantedQuery)
  if (wantedQuery !== lastQuery) {
    setLastQuery(wantedQuery)
    setQuery(wantedQuery)
  }
  const [page, setPage] = useState(0)
  const dateFilter = useDateFilter(() => setPage(0))
  const { range } = dateFilter

  // Adjusting state during the render rather than in an effect: React's own
  // answer to "reset something when a prop changes", and it avoids a first
  // paint of page four of a one-page result.
  const [lastKey, setLastKey] = useState(resetKey)
  if (resetKey !== lastKey) {
    setLastKey(resetKey)
    setPage(0)
  }

  const filtered = useMemo(() => {
    const all = rows ?? []
    const q = query.trim().toLowerCase()
    const byDate = dateOf && (range.from || range.to)
      ? all.filter((row) => {
          const day = localDay(dateOf(row))
          // A row with no date is not evidence that it is in range. It drops
          // out, and the pager's count says how many rows are being shown.
          if (!day) return false
          if (range.from && day < range.from) return false
          if (range.to && day > range.to) return false
          return true
        })
      : all
    if (!q) return byDate
    return byDate.filter((row) =>
      searchable(row).some((v) => v && v.toLowerCase().includes(q)))
    // `searchable` and `dateOf` are declared inline by every caller and would
    // change on each render; the rows, the query and the range are what
    // actually decide the result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, query, range])

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  // Clamped rather than stored: deleting the last row of the last page would
  // otherwise leave somebody on an empty page with no way back.
  const current = Math.min(page, pageCount - 1)
  const pageRows = filtered.slice(current * PAGE_SIZE, current * PAGE_SIZE + PAGE_SIZE)

  return {
    query,
    setQuery: (q: string) => { setQuery(q); setPage(0) },
    page: current,
    setPage,
    pageCount,
    pageRows,
    total: filtered.length,
    unfilteredTotal: (rows ?? []).length,
    dateFilter,
  }
}

/**
 * The range picker that sits beside the search box.
 *
 * The two date inputs appear only for a custom range: five of the six presets
 * need no dates at all, and two empty date fields on every register is the
 * kind of permanent clutter that makes a toolbar unreadable.
 */
export function DateRangeFilter({ preset, from, to, setPreset, setFrom, setTo }: DateFilter) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        className={inputSized('w-40')}
        value={preset}
        onChange={(e) => setPreset(e.target.value as RangePreset)}
        aria-label="Date range"
      >
        {RANGE_LABELS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
      </select>
      {preset === 'custom' && (
        <>
          <input
            type="date"
            className={inputSized('w-36')}
            value={from}
            max={to || undefined}
            onChange={(e) => setFrom(e.target.value)}
            aria-label="From date"
          />
          <span className="text-xs text-neutral-500">to</span>
          <input
            type="date"
            className={inputSized('w-36')}
            value={to}
            min={from || undefined}
            onChange={(e) => setTo(e.target.value)}
            aria-label="To date"
          />
        </>
      )}
    </div>
  )
}

export function SearchBox({ value, onChange, placeholder, children }: {
  value: string
  onChange: (v: string) => void
  /** Say what can be searched. "Search" alone makes people guess. */
  placeholder: string
  /** Filters that belong beside the box rather than above it. */
  children?: ReactNode
}) {
  return (
    <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center">
      <div className="relative min-w-0 flex-1">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400"
                aria-hidden />
        <input
          type="search"
          className="min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white pl-8 pr-3 text-sm text-neutral-900 focus:border-primary-400 focus:outline-none"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          aria-label={placeholder}
        />
      </div>
      {children}
    </div>
  )
}

/**
 * Nothing is drawn for a single page — a pager under four rows is furniture.
 *
 * `capped` is passed when the server's own limit was reached, and says so: the
 * list then describes what was fetched, not what exists.
 *
 * ★ The cap is reported as `loaded`, the number of rows that arrived — not as
 * `total`, which is what is left after the search and the date range. A note
 * reading "the newest 3 only" because three rows matched a search is worse
 * than no note. And it no longer suggests narrowing the search: searching and
 * filtering both happen in the browser, so neither can reach row 101.
 */
export function Pagination(
  { page, pageCount, total, onPage, capped = false, loaded, noun = 'rows' }: {
    page: number
    pageCount: number
    total: number
    onPage: (p: number) => void
    capped?: boolean
    /** How many rows the server actually sent, when it sent all it would. */
    loaded?: number
    noun?: string
  }) {
  if (pageCount <= 1 && !capped) return null

  const from = total === 0 ? 0 : page * PAGE_SIZE + 1
  const to = Math.min(total, (page + 1) * PAGE_SIZE)

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
      <p className="text-xs text-neutral-500">
        {total === 0
          ? `No ${noun}`
          : <>Showing <span className="font-medium text-neutral-700">{from}–{to}</span> of {total} {noun}</>}
        {capped && (
          <span className="ml-1 text-warning-700">
            — only the {loaded ?? total} most recent were loaded; older {noun} are
            not in this list
          </span>
        )}
      </p>

      {pageCount > 1 && (
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => onPage(page - 1)}
            disabled={page === 0}
            aria-label="Previous page"
            className="flex min-h-[36px] min-w-[36px] items-center justify-center rounded-md border border-neutral-200 text-neutral-600 hover:bg-neutral-50 disabled:opacity-30"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="px-1.5 text-xs tabular-nums text-neutral-600">
            {page + 1} / {pageCount}
          </span>
          <button
            type="button"
            onClick={() => onPage(page + 1)}
            disabled={page >= pageCount - 1}
            aria-label="Next page"
            className="flex min-h-[36px] min-w-[36px] items-center justify-center rounded-md border border-neutral-200 text-neutral-600 hover:bg-neutral-50 disabled:opacity-30"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  )
}
