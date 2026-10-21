/**
 * Plausibility checks for dates typed in — or read by the AI — on invoices and
 * agreement receipts (ISS-0075).
 *
 * The AI reads "07/12/2026" or "26/08/04" off a document and has to guess the
 * order. Guessed wrong, an invoice lands months in the future, or eighteen
 * years in the past and shows as "Overdue 7634d" for ever. These are WARNINGS,
 * like the document-total check (PRD INV-003a): the form asks the user to look
 * again and confirm, it never refuses — real back-dated and post-dated
 * documents exist.
 *
 * Every argument is a date-only string (YYYY-MM-DD) and every comparison is on
 * calendar days, never on Date objects: `new Date('2026-10-21')` is UTC
 * midnight, the previous evening in Toronto. `today` must be the user's own
 * calendar date — todayISODate() — not toISOString(), which is already
 * tomorrow from 20:00 Toronto time and would flag every evening's entry as
 * "in the future".
 */

export type DateWarningCode = 'future' | 'over_a_year_ago' | 'due_before_invoice' | 'before_valid_from'

export interface DateWarning {
  code: DateWarningCode
  message: string
}

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/

function parts(value: string): [number, number, number] | null {
  const m = DATE_ONLY.exec(value)
  if (!m) return null
  const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3])
  // Reject 2026-02-31 and the like rather than letting Date roll it over.
  const t = new Date(Date.UTC(y, mo - 1, d))
  if (t.getUTCFullYear() !== y || t.getUTCMonth() !== mo - 1 || t.getUTCDate() !== d) return null
  return [y, mo, d]
}

/** Calendar-day index; equal for the same date wherever the user is. */
function dayIndex(value: string): number | null {
  const p = parts(value)
  return p ? Date.UTC(p[0], p[1] - 1, p[2]) / 86_400_000 : null
}

const iso = (y: number, m: number, d: number) =>
  `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`

/** "dd/mm/yyyy" — how dates are shown everywhere else in EPMS (formatDate). */
function show(value: string): string {
  const p = parts(value)
  return p ? `${String(p[2]).padStart(2, '0')}/${String(p[1]).padStart(2, '0')}/${p[0]}` : value
}

/** The same date with day and month exchanged, when that is a different, real
 *  date that is not itself in the future — the likeliest misreading. */
function swappedDayMonth(value: string, today: string): string | null {
  const p = parts(value)
  if (!p) return null
  const [y, m, d] = p
  if (d > 12 || d === m) return null
  const swapped = iso(y, d, m)
  const s = dayIndex(swapped), t = dayIndex(today)
  if (s === null || t === null || s > t) return null
  return swapped
}

/** More than a year: the same date a year back is still after it. */
function overAYearBefore(value: string, today: string): boolean {
  const p = parts(today), v = dayIndex(value)
  if (!p || v === null) return false
  const yearAgo = dayIndex(iso(p[0] - 1, p[1], p[2] === 29 && p[1] === 2 ? 28 : p[2]))
  return yearAgo !== null && v < yearAgo
}

function futureWarning(what: string, value: string, today: string): DateWarning {
  const swapped = swappedDayMonth(value, today)
  return {
    code: 'future',
    message: `The ${what} ${show(value)} is in the future.` + (swapped
      ? ` Are day and month swapped? Read the other way it is ${show(swapped)}.`
      : ' Check it against the document.'),
  }
}

/** Invoice date and due date, checked against the upload date (today). */
export function invoiceDateWarnings(invoiceDate: string, dueDate: string, today: string): DateWarning[] {
  const out: DateWarning[] = []
  const inv = dayIndex(invoiceDate), now = dayIndex(today)
  if (inv === null || now === null) return out
  if (inv > now) {
    out.push(futureWarning('invoice date', invoiceDate, today))
  } else if (overAYearBefore(invoiceDate, today)) {
    out.push({
      code: 'over_a_year_ago',
      message: `The invoice date ${show(invoiceDate)} is more than a year ago. Is the year right?`,
    })
  }
  const due = dueDate ? dayIndex(dueDate) : null
  if (due !== null && due < inv) {
    out.push({
      code: 'due_before_invoice',
      message: `The due date ${show(dueDate)} is before the invoice date ${show(invoiceDate)}.`,
    })
  }
  return out
}

/** Agreement receipt date, checked against today and the agreement's Valid From. */
export function receiptDateWarnings(receiptDate: string, today: string, validFrom?: string | null): DateWarning[] {
  const out: DateWarning[] = []
  const r = dayIndex(receiptDate), now = dayIndex(today)
  if (r === null || now === null) return out
  if (r > now) {
    out.push(futureWarning('receipt date', receiptDate, today))
  }
  const from = validFrom ? dayIndex(validFrom.slice(0, 10)) : null
  if (from !== null && r < from) {
    out.push({
      code: 'before_valid_from',
      message: `The receipt date ${show(receiptDate)} is before the agreement starts (Valid From ${show(validFrom!.slice(0, 10))}). Is the year right?`,
    })
  }
  return out
}
