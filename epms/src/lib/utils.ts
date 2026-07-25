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

export function formatDate(date: Date | string | null | undefined): string {
  if (!date) return '—'
  const d = typeof date === 'string' ? new Date(date) : date
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
  const d = typeof date === 'string' ? new Date(date) : date
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
