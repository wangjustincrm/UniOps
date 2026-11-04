import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

/** A titled block: a card inside a tab (an address, a group of facts, a
 *  table), or with `plain` no card and the size of what is around it (a panel
 *  of a page, a part of a sheet that already has its frame). `label` names the
 *  region when the title is not plain text; `level` 2 for a page's own panels. */
export function Section({ title, actions, children, className, plain, label, level = 3 }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string
  plain?: boolean; label?: string; level?: 2 | 3
}) {
  const Heading = level === 2 ? 'h2' : 'h3'
  return (
    <section aria-label={label ?? (typeof title === 'string' ? title : undefined)}
      className={cn('flex min-w-0 flex-col gap-2', !plain && 'rounded-lg border border-neutral-200 bg-white p-3 sm:p-4', className)}>
      {(title || actions) && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          {title && <Heading className={cn('font-semibold text-neutral-800', !plain && 'text-sm')}>{title}</Heading>}
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}
