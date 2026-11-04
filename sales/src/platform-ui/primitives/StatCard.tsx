import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

const TONES = {
  neutral: 'border-neutral-200',
  warning: 'border-warning-500 bg-warning-50',
  danger: 'border-danger-600 bg-danger-50',
  success: 'border-success-500',
} as const

/** A count with its label (a dashboard or health card). With `onOpen` the
 *  whole card is a button that opens the list behind the count; `compact` is
 *  the small form, for a setting's value in a row above a list. */
export function StatCard({ label, count, tone = 'neutral', onOpen, children, className, compact }: {
  label: string; count: number | string; tone?: keyof typeof TONES; onOpen?: () => void; children?: ReactNode
  className?: string; compact?: boolean
}) {
  const body = (
    <>
      <span className="text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</span>
      <span className={cn('font-semibold text-neutral-900', compact ? 'truncate text-sm' : 'text-2xl')}>{count}</span>
      {children}
    </>
  )
  const cls = cn('flex min-w-0 flex-col items-start rounded-lg border bg-white text-left', compact ? 'gap-0.5 px-3 py-2' : 'gap-1 p-3', TONES[tone], className)
  return onOpen
    ? <button type="button" onClick={onOpen} className={cn(cls, 'hover:shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600')}>{body}</button>
    : <div className={cls}>{body}</div>
}
