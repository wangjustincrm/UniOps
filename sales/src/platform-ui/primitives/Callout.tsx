import { useState, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { cn } from '../lib/cn'

const TONE = {
  info: 'border-primary-200 bg-primary-50 text-primary-800',
  warning: 'border-warning-200 bg-warning-50 text-warning-800',
  danger: 'border-danger-200 bg-danger-50 text-danger-700',
  neutral: 'border-neutral-200 bg-neutral-50 text-neutral-700',
} as const

const CLOSED_KEY = 'platform-ui.callout.closed.'

function closedBefore(id: string | undefined): boolean {
  if (!id) return false
  try { return localStorage.getItem(CLOSED_KEY + id) === '1' } catch { return false }  // no storage: shown
}

/** A standing fact about the page or the record, shown where it applies — "on
 *  hold", "automatic runs are off", "login refused". Not news: it is not read
 *  aloud and does not come and go (what just happened is a notice, useNotices).
 *  `dismissible`: a ✕ closes it (for this visit); `once`: its stable id — closed, it is not shown again for this
 *  person in this browser (the user, 2026-11-27). Default: neither, it stays. */
export function Callout({ tone = 'warning', title, label, children, className, dismissible, once }: {
  tone?: keyof typeof TONE; title?: ReactNode; children?: ReactNode; className?: string
  /** Names it as a region (found and read by that name). */
  label?: string
  dismissible?: boolean
  /** A stable id: once closed, never shown again in this browser (implies dismissible). */
  once?: string
}) {
  const [closed, setClosed] = useState(() => closedBefore(once))
  if (closed) return null
  const close = () => {
    if (once) { try { localStorage.setItem(CLOSED_KEY + once, '1') } catch { /* no storage: closed for this visit only */ } }
    setClosed(true)
  }
  return (
    <section aria-label={label} className={cn('relative flex flex-col gap-1 rounded-lg border px-3 py-2 text-sm', TONE[tone], (dismissible || once) && 'pr-9', className)}>
      {title && <p className="font-medium">{title}</p>}
      {children}
      {(dismissible || once) && (
        <button type="button" aria-label={`Close${label ? ` ${label}` : ''}`} onClick={close}
          className="absolute right-1.5 top-1.5 inline-flex h-6 w-6 items-center justify-center rounded opacity-70 hover:bg-black/5 hover:opacity-100">
          <X aria-hidden className="h-3.5 w-3.5" />
        </button>
      )}
    </section>
  )
}
