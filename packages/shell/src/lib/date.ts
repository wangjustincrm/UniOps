// Same display rule as each app's src/lib/utils.ts formatDate (user decision
// 2026-11-11): YYYY-MMM-DD, e.g. 2026-NOV-11. A bare YYYY-MM-DD string is a
// calendar date and is read as a local date, so it never shifts by a day.
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return '—'
  let d: Date
  if (typeof value === 'string' && DATE_ONLY.test(value)) {
    const [y, m, day] = value.split('-').map(Number)
    d = new Date(y, m - 1, day)
  } else {
    d = typeof value === 'string' ? new Date(value) : value
  }
  if (isNaN(d.getTime())) return '—'
  return `${d.getFullYear()}-${MONTHS[d.getMonth()]}-${String(d.getDate()).padStart(2, '0')}`
}
