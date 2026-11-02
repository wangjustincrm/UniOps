import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

const TONES = {
  neutral: 'border-neutral-200',
  warning: 'border-warning-500 bg-warning-50',
  danger: 'border-danger-600 bg-danger-50',
  success: 'border-success-500',
} as const

/** A count with its label (a dashboard or health card). With `onOpen` the
 *  whole card is a button that opens the list behind the count. */
export function StatCard({ label, count, tone = 'neutral', onOpen, children, className }: {
  label: string; count: number | string; tone?: keyof typeof TONES; onOpen?: () => void; children?: ReactNode
  className?: string
}) {
  const body = (
    <>
      <span className="text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</span>
      <span className="text-2xl font-semibold text-neutral-900">{count}</span>
      {children}
    </>
  )
  const cls = cn('flex flex-col items-start gap-1 rounded-lg border bg-white p-3 text-left', TONES[tone], className)
  return onOpen
    ? <button type="button" onClick={onOpen} className={cn(cls, 'hover:shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600')}>{body}</button>
    : <div className={cls}>{body}</div>
}
