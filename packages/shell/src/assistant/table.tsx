/**
 * A table in an Assistant reply: collapsible, sortable by any column, and
 * filterable (ISS-0111).
 *
 * The panel is narrow and long titles wrap, so a ten-row answer could fill
 * several screens. The table can be folded to one line — open by default, as
 * the user decided — and its columns sorted (ascending / descending, arrow on
 * the header) and its rows filtered.
 *
 * What is sorted is the table the reply holds, which is not always the whole
 * result: an answer lists at most ten rows of up to a hundred (AST-003). When
 * the reply does not show that it holds every row, a sort or filter says so on
 * the table, so "largest first" is never read as the largest of all of them.
 * Export to Excel is untouched and still has every row.
 */
import React from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { renderInline } from './markdown'
import { SortTh, sortRows, useSort, type SortGetters, type SortValue } from '../ui/sortable'

/** A cell's text without the inline markup it may carry. */
function plain(cell: string): string {
  return (cell ?? '').replace(/\*\*|`/g, '').replace(/^\*(.*)\*$/, '$1').trim()
}

const EMPTY = /^(|-|—|–|n\/a|none)$/i
// 1,234.56 · $1,234.56 · 1,234.56 CAD · CAD 1,234.56 · -12 · 12.5% · (1,234.00)
const NUMBER = /^(?:[A-Z]{3}\s*)?[$€£¥]?\s*(\(?)\s*([-−+]?)\s*(\d[\d,\s]*(?:\.\d+)?|\.\d+)\s*(\)?)\s*%?\s*(?:[A-Z]{3})?$/
// The on-screen format (UI-DATE-002): 2026-NOV-11, optionally '14:30' after.
const YMMMD = /^(\d{4})-(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-(\d{1,2})(.*)$/i
const MONTH_NO: Record<string, string> = {
  JAN: '01', FEB: '02', MAR: '03', APR: '04', MAY: '05', JUN: '06',
  JUL: '07', AUG: '08', SEP: '09', OCT: '10', NOV: '11', DEC: '12',
}
// Day-first slashed dates: the model still writes them now and then.
const DMY = /^(\d{1,2})\/(\d{1,2})\/(\d{4})(.*)$/

/**
 * What a cell sorts by, for the shared list sort (ui/sortable): blank → null
 * (last either way), an amount → its value, a date (YYYY-MMM-DD as the pages
 * show dates, or a day-first dd/mm/yyyy) → ISO text, anything else → its text.
 * Without the YYYY-MMM-DD case the month NAME sorted as text: APR < AUG < DEC.
 */
export function sortKey(cell: string): SortValue {
  const s = plain(cell)
  if (EMPTY.test(s)) return null
  const m = NUMBER.exec(s)
  if (m) {
    const n = Number(m[3].replace(/[,\s]/g, ''))
    if (Number.isFinite(n)) {
      const negative = m[2] === '-' || m[2] === '−' || (m[1] === '(' && m[4] === ')')
      return negative ? -n : n
    }
  }
  const y = YMMMD.exec(s)
  if (y) return `${y[1]}-${MONTH_NO[y[2].toUpperCase()]}-${y[3].padStart(2, '0')}${y[4]}`
  const d = DMY.exec(s)
  if (d) return `${d[3]}-${d[2].padStart(2, '0')}-${d[1].padStart(2, '0')}${d[4]}`
  return s
}

/** A closing "Total" row stays at the bottom and out of sorting and filtering. */
const TOTAL = /^(total|grand total|sum|合计|总计|小计|总额|共计)\b|^(合计|总计|小计|总额|共计)/i

export function splitTotal(rows: string[][]): { body: string[][]; total: string[] | null } {
  const last = rows[rows.length - 1]
  if (rows.length > 1 && last && TOTAL.test(plain(last[0] ?? ''))) {
    return { body: rows.slice(0, -1), total: last }
  }
  return { body: rows, total: null }
}

export function filterRows(rows: string[][], filter: string): string[][] {
  const f = filter.trim().toLowerCase()
  if (!f) return rows
  return rows.filter((r) => r.some((c) => plain(c).toLowerCase().includes(f)))
}

/** Show the filter box from this many rows on. */
const FILTER_FROM_ROWS = 4

export function AnswerTable({
  head, rows, resultRows, keyBase = 't',
}: {
  head: string[]
  rows: string[][]
  /**
   * How many rows the whole result has, when the reply says. Only when the
   * table holds all of them is a sort a sort of the whole result.
   */
  resultRows?: number | null
  keyBase?: string
}) {
  const [open, setOpen] = React.useState(true)
  // The same click-to-sort header as the EPMS list pages.
  const [sort, onSort] = useSort<string>(null)
  const [filter, setFilter] = React.useState('')

  const { body, total } = splitTotal(rows)
  const holdsAll = resultRows != null && body.length >= resultRows
  const kept = filterRows(body, filter)
  const getters: SortGetters<string[], string> = Object.fromEntries(
    head.map((_, ci) => [String(ci), (r: string[]) => sortKey(r[ci] ?? '')]))
  const shown = sortRows(kept, sort, getters)
  const rowsLabel = `${body.length} row${body.length === 1 ? '' : 's'}`

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={false}
        className="inline-flex items-center gap-1 rounded border border-neutral-200 bg-white px-2 py-1 text-xs text-neutral-700 hover:border-primary-600 hover:text-primary-600"
      >
        <ChevronRight className="h-3 w-3" aria-hidden />
        {rowsLabel} — show table
      </button>
    )
  }

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-neutral-500">
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-expanded
          className="inline-flex items-center gap-0.5 hover:text-primary-600"
        >
          <ChevronDown className="h-3 w-3" aria-hidden />
          {rowsLabel} — hide table
        </button>
        {body.length >= FILTER_FROM_ROWS && (
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter rows"
            aria-label="Filter rows"
            className="h-6 min-w-0 flex-1 rounded border border-neutral-200 bg-white px-1.5 text-[11px] text-neutral-800 focus:border-primary-600 focus:outline-none"
          />
        )}
      </div>
      {(sort || filter.trim()) && !holdsAll && (
        <p role="note" className="text-[11px] text-warning-700">
          {`Sorting and the filter apply only to the ${rowsLabel} shown here`}
          {resultRows != null && resultRows > body.length
            ? `, not the whole result (${resultRows} rows) — Export to Excel has every row.`
            : ', not necessarily the whole result.'}
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse text-xs">
          <thead>
            <tr>{head.map((c, ci) => (
              <SortTh
                key={ci}
                sortKey={String(ci)}
                sort={sort}
                onSort={onSort}
                title={`Sort by ${plain(c)}`}
                className="border-b border-neutral-300 px-2 py-1 text-left font-semibold"
              >
                {renderInline(c, `${keyBase}-h${ci}`)}
              </SortTh>
            ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, ri) => (
              <tr key={ri} className="border-b border-neutral-200 last:border-0">
                {head.map((_, ci) => (
                  <td key={ci} className="px-2 py-1 align-top">{renderInline(r[ci] ?? '', `${keyBase}-${ri}-${ci}`)}</td>
                ))}
              </tr>
            ))}
            {shown.length === 0 && (
              <tr><td colSpan={head.length} className="px-2 py-1 text-neutral-500">No rows match the filter.</td></tr>
            )}
            {total && (
              <tr className="border-t border-neutral-300 font-semibold">
                {head.map((_, ci) => (
                  <td key={ci} className="px-2 py-1 align-top">{renderInline(total[ci] ?? '', `${keyBase}-t-${ci}`)}</td>
                ))}
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
