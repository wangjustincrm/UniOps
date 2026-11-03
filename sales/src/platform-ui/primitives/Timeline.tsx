import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

export interface TimelineItem {
  key: string | number
  /** When, already formatted. */
  when: ReactNode
  who?: ReactNode
  /** What happened, one line. */
  title: ReactNode
  /** More: a before → after, a reason. */
  body?: ReactNode
  tone?: 'neutral' | 'info' | 'warning' | 'danger' | 'success'
}

const TONE = {
  neutral: 'bg-neutral-300', info: 'bg-primary-500', warning: 'bg-warning-500', danger: 'bg-danger-600', success: 'bg-success-500',
} as const

/** What happened to a record, newest first: a vertical line of events, each
 *  with when, who, what and its detail. */
export function Timeline({ items, ariaLabel = 'Activity', className }: { items: TimelineItem[]; ariaLabel?: string; className?: string }) {
  return (
    <ol aria-label={ariaLabel} className={cn('relative flex flex-col', className)}>
      {items.map((it, i) => (
        <li key={it.key} className="relative flex gap-3 pb-4 last:pb-0">
          {i < items.length - 1 && <span aria-hidden className="absolute left-[5px] top-4 h-full w-px bg-neutral-200" />}
          <span aria-hidden className={cn('relative mt-1.5 h-[11px] w-[11px] shrink-0 rounded-full ring-2 ring-white', TONE[it.tone ?? 'neutral'])} />
          <div className="flex min-w-0 flex-col gap-0.5 text-sm">
            <p className="text-neutral-800">{it.title}</p>
            <p className="text-xs text-neutral-500">{it.when}{it.who ? <> · {it.who}</> : null}</p>
            {it.body && <div className="text-xs text-neutral-600">{it.body}</div>}
          </div>
        </li>
      ))}
    </ol>
  )
}
