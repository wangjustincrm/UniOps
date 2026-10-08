import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatAmount(amount: number | string, currency = 'CAD'): string {
  const n = typeof amount === 'string' ? parseFloat(amount) : amount
  if (isNaN(n)) return '—'
  return new Intl.NumberFormat('en-CA', {
    style: 'currency', currency, minimumFractionDigits: 2,
  }).format(n)
}

// A date-only string ("2026-07-30") carries no time zone. `new Date()` parses it
// as UTC midnight, and rendering that with a local formatter anywhere west of
// Greenwich shows the PREVIOUS day — this company is UTC-4/-5, so every
// date-only field in the app displayed one day early. Reported against an
// agreement's payment schedule (expected 2026-07-30, shown as 29/07/2026), but
// it applied to invoice dates, due dates, validity windows and everything else
// stored as a plain date.
//
// Strings that carry a time are left alone: those are real instants, and
// showing them in the reader's own zone is correct.
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

function toLocalDate(value: Date | string): Date {
  if (typeof value !== 'string') return value
  if (!DATE_ONLY.test(value)) return new Date(value)
  const [y, m, d] = value.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function formatDate(date: string | Date | null | undefined): string {
  if (!date) return '—'
  const d = toLocalDate(date)
  if (isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-CA', { year: 'numeric', month: 'short', day: 'numeric' })
}

// The same UTC/local trap as above, on the writing side rather than the reading
// side. Every create form seeded its date field with
// `new Date().toISOString().slice(0, 10)`, which is the UTC date — so from
// 20:00 Toronto time onward, a claim raised tonight was stamped TOMORROW. It
// went to the approver, to the accounting period, and to the age of the
// document, all one day out, for the last four hours of every working day.
export function todayLocal(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// Calendar year at the reader's desk. `getUTCFullYear()` rolls over at 20:00 on
// 31 December here, which pointed the budget-account lookup at the next fiscal
// year while people were still booking against this one.
export function currentYearLocal(): number {
  return new Date().getFullYear()
}
