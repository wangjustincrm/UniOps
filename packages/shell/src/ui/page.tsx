/** Page-level building blocks: header, sections, toolbars. */
import React from 'react'
import { cn } from '../lib/cn'

export interface PageHeaderProps {
  title: React.ReactNode
  /** One line of context under the title. */
  description?: React.ReactNode
  /** Status badge / doc number shown next to the title. */
  meta?: React.ReactNode
  /** Back link (TabBackLink) above the title. */
  back?: React.ReactNode
  /** Page actions, right side. One primary at most. */
  actions?: React.ReactNode
  className?: string
}

export function PageHeader({ title, description, meta, back, actions, className }: PageHeaderProps) {
  return (
    <header className={cn('mb-6 flex flex-col gap-3', className)}>
      {back && <div className="-mb-1">{back}</div>}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold text-fg">{title}</h1>
            {meta}
          </div>
          {description && <p className="mt-1 text-sm text-fg-muted">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  )
}

/** Main content column of an app page. */
export function PageBody({ className, children, width = 'wide' }: { className?: string; children: React.ReactNode; width?: 'narrow' | 'normal' | 'wide' | 'full' }) {
  const w = { narrow: 'max-w-3xl', normal: 'max-w-5xl', wide: 'max-w-[1600px]', full: 'max-w-none' }[width]
  return <div className={cn('mx-auto w-full px-6 py-6', w, className)}>{children}</div>
}

/** A titled block on a page (no card chrome). */
export function Section({ title, description, actions, className, children }: {
  title?: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode; className?: string; children: React.ReactNode
}) {
  return (
    <section className={cn('flex flex-col gap-3', className)}>
      {(title || actions) && (
        <div className="flex items-end justify-between gap-3">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-fg">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-fg-muted">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

/** Filter / action bar above a list. */
export function Toolbar({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn('mb-4 flex flex-wrap items-center gap-2', className)}>{children}</div>
}

/** Small quiet label above a group of fields or values. Sentence case. */
export function Eyebrow({ className, children }: { className?: string; children: React.ReactNode }) {
  return <p className={cn('text-xs font-medium text-fg-muted', className)}>{children}</p>
}
