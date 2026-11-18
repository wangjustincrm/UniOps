/** Figures and identifiers: Amount, DocNumber, Stat, DescriptionList, Tabs. */
import React from 'react'
import { cn } from '../lib/cn'

/**
 * A money amount. Mono, tabular, one format everywhere:
 * 2 decimals, thousands separators, currency code after when given.
 */
export function formatAmount(value: number | string | null | undefined, opts: { decimals?: number } = {}): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return String(value)
  const d = opts.decimals ?? 2
  return n.toLocaleString('en-CA', { minimumFractionDigits: d, maximumFractionDigits: d })
}

export function Amount({ value, currency, decimals, className, tone }: {
  value: number | string | null | undefined
  currency?: string | null
  decimals?: number
  className?: string
  /** Colour negatives red / positives green when the sign carries meaning. */
  tone?: 'auto' | 'none'
}) {
  const n = typeof value === 'number' ? value : Number(value)
  const color = tone === 'auto' && Number.isFinite(n) ? (n < 0 ? 'text-danger-fg' : n > 0 ? 'text-success-fg' : undefined) : undefined
  return (
    <span className={cn('whitespace-nowrap font-mono tabular-nums', color, className)}>
      {formatAmount(value, { decimals })}
      {currency && value !== null && value !== undefined && value !== '' && <span className="ml-1 text-fg-muted">{currency}</span>}
    </span>
  )
}

/** The UniOps signature: document numbers (PO-…, PA-…, AP-…) in one mono style. */
export function DocNumber({ children, className, as: As = 'span', ...props }: {
  children: React.ReactNode; className?: string; as?: 'span' | 'code'
} & React.HTMLAttributes<HTMLElement>) {
  return (
    <As className={cn('whitespace-nowrap font-mono text-[0.92em] tracking-normal text-fg-secondary', className)} {...props}>
      {children}
    </As>
  )
}

/** Stat card: label, big figure, optional change with what it compares to. */
export function Stat({ label, value, change, changeTone = 'neutral', hint, className, onClick, active }: {
  label: React.ReactNode
  value: React.ReactNode
  change?: React.ReactNode
  changeTone?: 'neutral' | 'good' | 'bad'
  hint?: React.ReactNode
  className?: string
  onClick?: () => void
  /** Selected as a filter. */
  active?: boolean
}) {
  const Comp = onClick ? 'button' : 'div'
  return (
    <Comp
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      aria-pressed={onClick ? !!active : undefined}
      className={cn(
        'flex flex-col gap-1 rounded-xl border bg-surface px-5 py-4 text-left',
        active ? 'border-accent shadow-[inset_0_0_0_1px_var(--color-accent)]' : 'border-line-subtle',
        onClick && 'transition-colors hover:border-line-strong',
        className,
      )}
    >
      <span className="text-xs font-medium text-fg-muted">{label}</span>
      <span className="font-mono text-2xl font-medium tabular-nums text-fg">{value}</span>
      {change && (
        <span className={cn('text-xs', changeTone === 'good' ? 'text-success-fg' : changeTone === 'bad' ? 'text-danger-fg' : 'text-fg-muted')}>{change}</span>
      )}
      {hint && <span className="text-xs text-fg-muted">{hint}</span>}
    </Comp>
  )
}

/** Label/value pairs on detail pages. */
export function DescriptionList({ items, columns = 2, className }: {
  items: Array<{ label: React.ReactNode; value: React.ReactNode; span?: 1 | 2 | 3 | 4; key?: string }>
  columns?: 1 | 2 | 3 | 4
  className?: string
}) {
  const cols = { 1: 'sm:grid-cols-1', 2: 'sm:grid-cols-2', 3: 'sm:grid-cols-3', 4: 'sm:grid-cols-4' }[columns]
  const span = { 1: '', 2: 'sm:col-span-2', 3: 'sm:col-span-3', 4: 'sm:col-span-4' }
  return (
    <dl className={cn('grid grid-cols-1 gap-x-6 gap-y-4', cols, className)}>
      {items.map((it, i) => (
        <div key={it.key ?? i} className={cn('min-w-0', it.span && span[it.span])}>
          <dt className="text-xs font-medium text-fg-muted">{it.label}</dt>
          <dd className="mt-1 break-words text-sm text-fg">{it.value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  )
}

export interface TabItem<K extends string = string> { key: K; label: React.ReactNode; count?: number; disabled?: boolean }

/** Underline tabs for switching views on one page. */
export function Tabs<K extends string>({ items, value, onChange, className }: {
  items: TabItem<K>[]; value: K; onChange: (k: K) => void; className?: string
}) {
  return (
    <div role="tablist" className={cn('flex gap-1 overflow-x-auto border-b border-line', className)}>
      {items.map((t) => {
        const on = t.key === value
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={on}
            disabled={t.disabled}
            onClick={() => onChange(t.key)}
            className={cn(
              '-mb-px inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm transition-colors disabled:opacity-40',
              on ? 'border-accent font-medium text-fg' : 'border-transparent text-fg-muted hover:text-fg',
            )}
          >
            {t.label}
            {t.count !== undefined && <span className="font-mono text-xs tabular-nums text-fg-muted">{t.count}</span>}
          </button>
        )
      })}
    </div>
  )
}

/** Pill-free segmented control for 2–4 options (filters, modes). */
export function Segmented<K extends string>({ items, value, onChange, className, size = 'sm' }: {
  items: Array<{ key: K; label: React.ReactNode }>; value: K; onChange: (k: K) => void; className?: string; size?: 'sm' | 'md'
}) {
  return (
    <div role="radiogroup" className={cn('inline-flex rounded-lg border border-line bg-surface-subtle p-0.5', className)}>
      {items.map((it) => {
        const on = it.key === value
        return (
          <button
            key={it.key}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(it.key)}
            className={cn(
              'rounded-md px-3 font-medium transition-colors',
              size === 'sm' ? 'h-7 text-xs' : 'h-8 text-sm',
              on ? 'bg-surface text-fg shadow-xs' : 'text-fg-muted hover:text-fg',
            )}
          >
            {it.label}
          </button>
        )
      })}
    </div>
  )
}
