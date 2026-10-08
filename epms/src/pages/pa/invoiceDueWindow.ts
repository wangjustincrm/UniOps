// Several invoices may share one PO-based payment application only if they
// fall due within a week of each other — one PA is one transfer on one payment
// date. Agreement PAs are exempt (they routinely pay a run of statements).
// The server enforces this (epms-api pa.py::PA_INVOICE_DUE_WINDOW_DAYS); this
// copy only lets the form say so before submit instead of after a 422.
export const PA_INVOICE_DUE_WINDOW_DAYS = 7

interface DueDated {
  internal_ref: string
  due_date: string
}

// due_date is a plain 'YYYY-MM-DD'. Parsing it with new Date() would read it as
// UTC midnight; counting in UTC days on both sides keeps the subtraction exact.
function dayNumber(isoDate: string): number {
  const [y, m, d] = isoDate.slice(0, 10).split('-').map(Number)
  return Date.UTC(y, m - 1, d) / 86_400_000
}

export interface DueWindowViolation<T extends DueDated> {
  earliest: T
  latest: T
  spanDays: number
}

export function dueWindowViolation<T extends DueDated>(invoices: T[]): DueWindowViolation<T> | null {
  if (invoices.length < 2) return null
  let earliest = invoices[0]
  let latest = invoices[0]
  for (const inv of invoices) {
    if (dayNumber(inv.due_date) < dayNumber(earliest.due_date)) earliest = inv
    if (dayNumber(inv.due_date) > dayNumber(latest.due_date)) latest = inv
  }
  const spanDays = dayNumber(latest.due_date) - dayNumber(earliest.due_date)
  return spanDays > PA_INVOICE_DUE_WINDOW_DAYS ? { earliest, latest, spanDays } : null
}

// The default selection when the form pre-ticks invoices: the earliest-due one
// and every other candidate due within the window of it. Pre-ticking all of
// them would propose a payment the server refuses whenever they span more
// than a week — the most urgent group is the one to pay first.
export function earliestDueGroup<T extends DueDated>(invoices: T[]): T[] {
  if (invoices.length === 0) return []
  const first = Math.min(...invoices.map((inv) => dayNumber(inv.due_date)))
  return invoices.filter((inv) => dayNumber(inv.due_date) - first <= PA_INVOICE_DUE_WINDOW_DAYS)
}
