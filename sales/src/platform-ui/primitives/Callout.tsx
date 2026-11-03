import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

const TONE = {
  info: 'border-primary-200 bg-primary-50 text-primary-800',
  warning: 'border-warning-200 bg-warning-50 text-warning-800',
  danger: 'border-danger-200 bg-danger-50 text-danger-700',
  neutral: 'border-neutral-200 bg-neutral-50 text-neutral-700',
} as const

/** A standing fact about the page or the record, shown where it applies — "on
 *  hold", "automatic runs are off", "login refused". Not news: it is not read
 *  aloud and does not come and go (what just happened is a notice, useNotices). */
export function Callout({ tone = 'warning', title, label, children, className }: {
  tone?: keyof typeof TONE; title?: ReactNode; children?: ReactNode; className?: string
  /** Names it as a region (found and read by that name). */
  label?: string
}) {
  return (
    <section aria-label={label} className={cn('flex flex-col gap-1 rounded-lg border px-3 py-2 text-sm', TONE[tone], className)}>
      {title && <p className="font-medium">{title}</p>}
      {children}
    </section>
  )
}
