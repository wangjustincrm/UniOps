import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatCAD(amount: number): string {
  return new Intl.NumberFormat('en-CA', {
    style: 'currency',
    currency: 'CAD',
    minimumFractionDigits: 2,
  }).format(amount)
}

/** Format a monetary amount for the given currency.
 *  Handles built-in codes plus any valid ISO 4217 code (e.g. GBP, JPY, SGD).
 *  Custom codes that aren't valid ISO 4217 fall back to "CODE 1,234.56" format.
 */
export function formatAmount(amount: number, currency: string): string {
  // RMB is a colloquial code (ISO code is CNY); handle it explicitly
  if (currency === 'RMB') {
    return '¥\u00A0' + new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount)
  }
  // Try Intl with the currency code directly — works for all ISO 4217 codes
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
    }).format(amount)
  } catch {
    // Fallback for non-ISO custom codes: "CODE 1,234.56"
    const num = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount)
    return `${currency}\u00A0${num}`
  }
}

export function todayISODate(): string {
  return new Date().toISOString().slice(0, 10)
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

export function formatDate(date: Date | string | null | undefined): string {
  if (!date) return '—'
  const d = toLocalDate(date)
  if (isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
}

export function formatCADCompact(amount: number): string {
  const abs = Math.abs(amount)
  const sign = amount < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}CA$${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${sign}CA$${(abs / 1_000).toFixed(0)}K`
  return formatCAD(amount)
}

/** Human-readable file size, e.g. "512 B", "3.4 KB", "1.2 MB". */
export function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / (1024 * 1024)).toFixed(1)} MB`
}

export function formatDateTime(date: Date | string | null | undefined): string {
  if (!date) return '—'
  const d = toLocalDate(date)
  if (isNaN(d.getTime())) return '—'
  const datePart = d.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
  const timePart = d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
  })
  return `${datePart} ${timePart}`
}
