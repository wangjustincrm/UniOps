import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDate(date: string | Date | null | undefined): string {
  if (!date) return '—'
  const d = typeof date === 'string' ? new Date(date) : date
  if (isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-CA', { year: 'numeric', month: 'short', day: 'numeric' })
}

export function formatDateTime(date: string | Date | null | undefined): string {
  if (!date) return '—'
  const d = typeof date === 'string' ? new Date(date) : date
  if (isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-CA', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

/** Format an ISO timestamp as relative time. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso).getTime()
  if (isNaN(d)) return '—'
  const seconds = Math.round((Date.now() - d) / 1000)
  if (seconds < 60)         return 'just now'
  if (seconds < 3600)       return `${Math.round(seconds / 60)}m ago`
  if (seconds < 86400)      return `${Math.round(seconds / 3600)}h ago`
  if (seconds < 86400 * 7)  return `${Math.round(seconds / 86400)}d ago`
  return new Date(iso).toLocaleDateString('en-CA')
}
