/**
 * The handful of shapes every page in this module repeats.
 *
 * Extracted when the fifth page was about to declare its own `inputClass` and
 * its own tab strip. Everything here is presentational and has no opinion about
 * data — the pages keep their own queries.
 */
import type { ReactNode } from 'react'

export const inputClass =
  'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
  'text-neutral-900 focus:border-primary-400 focus:outline-none'

export const buttonClass =
  'flex min-h-[40px] items-center justify-center gap-1.5 rounded-lg px-3 text-sm font-semibold ' +
  'transition-colors disabled:cursor-not-allowed disabled:opacity-50'

export const primaryButton = `${buttonClass} bg-primary-600 text-white hover:bg-primary-700`
export const secondaryButton =
  `${buttonClass} border border-neutral-300 bg-white text-neutral-700 hover:bg-neutral-50`
export const dangerButton = `${buttonClass} bg-danger-600 text-white hover:bg-danger-700`

export function PageHeader(
  { title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode },
) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-lg font-bold tracking-tight text-neutral-900">{title}</h1>
        {subtitle && <p className="mt-0.5 text-xs text-neutral-500">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  )
}

export function Tabs<T extends string>(
  { tabs, active, onChange }: {
    tabs: readonly { key: T; label: string; count?: number }[]
    active: T
    onChange: (key: T) => void
  },
) {
  return (
    <div className="mb-4 flex gap-1 overflow-x-auto border-b border-neutral-200">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          onClick={() => onChange(tab.key)}
          aria-current={tab.key === active ? 'page' : undefined}
          className={
            'min-h-[40px] shrink-0 border-b-2 px-3 text-sm font-medium transition-colors ' +
            (tab.key === active
              ? 'border-primary-600 text-primary-700'
              : 'border-transparent text-neutral-500 hover:text-neutral-800')
          }
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className="ml-1.5 rounded-full bg-neutral-100 px-1.5 py-0.5 text-[11px] tabular-nums text-neutral-600">
              {tab.count}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-neutral-200 bg-white p-3.5 ${className}`}>
      {children}
    </div>
  )
}

export function Field(
  { label, hint, children }: { label: string; hint?: string; children: ReactNode },
) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-neutral-700">{label}</span>
      {children}
      {hint && <span className="text-xs text-neutral-500">{hint}</span>}
    </label>
  )
}

/**
 * What to show when there is nothing.
 *
 * Takes a sentence, not a word: "Nothing here" tells a person the page loaded
 * and nothing else. Saying what would put something here is the difference
 * between an empty screen and a broken one.
 */
export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-xl border border-dashed border-neutral-300 p-6 text-center text-sm text-neutral-500">
      {children}
    </p>
  )
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null
  const message =
    (error as { body?: { detail?: string } })?.body?.detail ??
    (error as Error)?.message ??
    'Something went wrong.'
  return (
    <p className="mt-2 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{message}</p>
  )
}
