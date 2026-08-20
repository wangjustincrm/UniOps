/**
 * Due-date ageing for the invoice list.
 *
 * Everything here works in CALENDAR days, never in elapsed milliseconds:
 *
 *  - a stored `due_date` is a date-only string with no zone, so `new Date(s)`
 *    reads it as UTC midnight — the previous local day for this company
 *    (UTC-4/-5). See toLocalDate() in ./utils for the same trap.
 *  - `today` is a real instant, so it has to be flattened to its own calendar
 *    day before the two can be compared.
 *  - dividing elapsed ms by 86_400_000 is off by one across a DST boundary,
 *    so both ends are converted to a day index via Date.UTC instead.
 */

/** Days of remaining runway that still count as "due soon" (amber). */
export const DUE_SOON_DAYS = 5

export type DueTone =
  | 'overdue'  // past due and still owed
  | 'soon'     // due today, or within DUE_SOON_DAYS
  | 'normal'   // further out — no warning
  | 'settled'  // already paid; ageing is no longer meaningful

export interface DueInfo {
  tone: DueTone
  /** Calendar days from today to the due date; negative when overdue. */
  days: number | null
  /** Short badge text; empty when nothing should be badged. */
  label: string
}

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/

function dayIndexFromDateOnly(value: string): number | null {
  const m = DATE_ONLY.exec(value.slice(0, 10))
  if (!m) return null
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) / 86_400_000
}

function dayIndexFromInstant(d: Date): number {
  return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86_400_000
}

/**
 * `status` is the invoice's own status: a paid invoice is never warned about,
 * however long ago it fell due — otherwise the whole column reads red on
 * historic rows and the warning stops meaning anything.
 */
export function dueInfo(
  dueDate: string | null | undefined,
  status: string,
  today: Date = new Date(),
): DueInfo {
  if (!dueDate) return { tone: 'normal', days: null, label: '' }

  const due = dayIndexFromDateOnly(dueDate)
  if (due === null) return { tone: 'normal', days: null, label: '' }

  const days = due - dayIndexFromInstant(today)

  if (status === 'paid') return { tone: 'settled', days, label: '' }
  if (days < 0) return { tone: 'overdue', days, label: `Overdue ${-days}d` }
  if (days === 0) return { tone: 'soon', days, label: 'Due today' }
  if (days <= DUE_SOON_DAYS) return { tone: 'soon', days, label: `Due in ${days}d` }
  return { tone: 'normal', days, label: '' }
}
