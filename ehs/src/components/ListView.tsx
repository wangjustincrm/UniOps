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

export const PAGE_SIZE = 20

/** What a row contributes to the search. Null and undefined are skipped. */
export type Searchable<T> = (row: T) => (string | null | undefined)[]

export function useListView<T>(rows: T[] | undefined, searchable: Searchable<T>) {
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)

  const filtered = useMemo(() => {
    const all = rows ?? []
    const q = query.trim().toLowerCase()
    if (!q) return all
    return all.filter((row) =>
      searchable(row).some((v) => v && v.toLowerCase().includes(q)))
    // `searchable` is declared inline by every caller and would change on each
    // render; the rows and the query are what actually decide the result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, query])

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
  }
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
 * `capped` is passed when the server's own limit was reached, and says so:
 * the count then describes what was fetched, not what exists.
 */
export function Pagination({ page, pageCount, total, onPage, capped = false, noun = 'rows' }: {
  page: number
  pageCount: number
  total: number
  onPage: (p: number) => void
  capped?: boolean
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
            — the newest {total} only; narrow the search to reach older ones
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
