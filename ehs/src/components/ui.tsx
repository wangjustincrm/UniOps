/**
 * The handful of shapes every page in this module repeats.
 *
 * Extracted when the fifth page was about to declare its own `inputClass` and
 * its own tab strip. Everything here is presentational and has no opinion about
 * data — the pages keep their own queries.
 */
import type { ReactNode } from 'react'
import { ApiError } from '@/lib/api'

/**
 * The scale. Three sizes below the body text and no more:
 *
 *   text-sm    (14px)  body, controls, table cells
 *   text-xs    (12px)  labels, hints, secondary lines
 *   text-[11px]        a badge or a column heading, and nothing longer
 *
 * Anything smaller was in here — 9px and 10px, on text people were expected to
 * read — and is gone. The rounding scale is as short: `rounded-md` on a
 * control, `rounded-lg` on an input or button, `rounded-xl` on a card,
 * `rounded-full` on a pill. Six radii were in use, which reads as carelessness
 * long before anyone can name why.
 */

export const inputClass =
  'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
  'text-neutral-900 focus:border-primary-400 focus:outline-none'

/**
 * The same input at a fixed width.
 *
 * ★ Appending `w-40` to `inputClass` does nothing. Tailwind decides between
 * two width utilities by where they land in the generated stylesheet, not by
 * the order they appear in the string, and `w-full` is emitted after the
 * numbered widths — so it wins every time. Nine places in this module were
 * quietly full width because of it, which is why the two date pickers on the
 * audit pack stacked instead of sitting beside the Print button.
 */
export const inputSized = (width: string) =>
  `${inputClass.replace('w-full ', '')} ${width}`

export const buttonClass =
  'flex min-h-[40px] items-center justify-center gap-1.5 rounded-lg px-3 text-sm font-semibold ' +
  'transition-colors disabled:cursor-not-allowed disabled:opacity-50'

export const primaryButton = `${buttonClass} bg-primary-600 text-white hover:bg-primary-700`
export const secondaryButton =
  `${buttonClass} border border-neutral-300 bg-white text-neutral-700 hover:bg-neutral-50`
export const dangerButton = `${buttonClass} bg-danger-600 text-white hover:bg-danger-700`

export function PageHeader(
  // `subtitle` takes a node, not a string: several pages put a link or a
  // piece of regulation in it, and a string prop pushed them into hand-rolling
  // the whole header rather than using this.
  { title, subtitle, children }: { title: string; subtitle?: ReactNode; children?: ReactNode },
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
  // ★ The message, never `body.detail` itself. A validation refusal's detail
  // is a list of objects, and handed to React as a child it took the whole tab
  // down — round 6, TC-05 and TC-16: "Objects are not valid as a React child
  // (found: object with keys {type, loc, msg, input, ctx})". ApiError has
  // already turned it into words.
  const message =
    (typeof (error as Error)?.message === 'string' && (error as Error).message) ||
    'Something went wrong.'
  return (
    <p className="mt-2 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{message}</p>
  )
}


/**
 * The heading above a group of things on a page.
 *
 * Forty-eight copies of the same four utility classes were in the pages, which
 * is how a heading ends up one weight lighter on one screen than another and
 * nobody can say which is right.
 */
export function SectionTitle({ children, className = '' }: {
  children: ReactNode; className?: string
}) {
  return (
    <h2 className={`mb-2 text-[11px] font-bold uppercase tracking-wider text-neutral-400 ${className}`}>
      {children}
    </h2>
  )
}

/**
 * A status pill.
 *
 * `tone` names what it means rather than what colour it is, so the same state
 * reads the same on every page — and so a theme change is one file. Colour is
 * never the only signal: every pill carries its own words.
 */
const PILL_TONE = {
  neutral: 'bg-neutral-100 text-neutral-600',
  ok: 'bg-success-50 text-success-700',
  warn: 'bg-warning-50 text-warning-700',
  bad: 'bg-danger-50 text-danger-700',
  info: 'bg-info-50 text-info-700',
  /** Reserved for the one state that starts a statutory clock. */
  urgent: 'bg-danger-600 text-white',
} as const

export function Pill({ tone = 'neutral', children, className = '' }: {
  tone?: keyof typeof PILL_TONE; children: ReactNode; className?: string
}) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${PILL_TONE[tone]} ${className}`}>
      {children}
    </span>
  )
}

/**
 * A button that reads as a link.
 *
 * A real `<button>`, not an anchor with no href: the nine hand-rolled copies
 * of this were a mix of both, and the anchors could not be reached by keyboard
 * or fired by the space bar. The minimum height keeps it a touch target on a
 * phone even though it looks like text.
 */
export function LinkButton({ onClick, children, tone = 'primary', disabled, className = '', ...rest }: {
  onClick: () => void
  children: ReactNode
  tone?: 'primary' | 'quiet' | 'danger'
  disabled?: boolean
  className?: string
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onClick' | 'className'>) {
  const tones = {
    primary: 'text-primary-700 hover:underline',
    quiet: 'text-neutral-500 hover:text-neutral-900 hover:underline',
    danger: 'text-danger-700 hover:underline',
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex min-h-[32px] items-center gap-1 text-xs font-semibold disabled:opacity-40 ${tones[tone]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}


/**
 * Something on this page did not load.
 *
 * ★ For the very common shape `query.data ?? []`, which turns a failed or
 * refused request into an empty list. An empty list and a refused one mean
 * opposite things — "there is nothing" against "you were not shown it" — and
 * on a signature list the difference is whether somebody signs again. Render
 * this beside any list whose query can fail; the fallback stays, so the page
 * still draws.
 */
export function LoadFailedNote({ what, children }: {
  /** What could not be loaded, in the reader's words: "the signatures". */
  what?: string
  children?: ReactNode
}) {
  return (
    <p className="mt-3 rounded-lg bg-warning-50 px-3 py-2 text-xs text-warning-700">
      {children ?? (
        <>
          {what ? `${what} could not be loaded` : 'Some of this could not be loaded'},
          so what is shown may be incomplete. Nothing already recorded has been lost.
        </>
      )}
    </p>
  )
}


/**
 * Why a record is not on screen.
 *
 * ★ These pages read `if (!query.data) return "Not found"`, and a request that
 * was refused or that failed leaves `data` undefined just as a deleted record
 * does. So a 403 on somebody else's permit, and a 500, both rendered "Not
 * found" — telling the reader the record does not exist when it does, which is
 * the version of this mistake that sends somebody off to re-enter it.
 *
 * Gone and unreachable are different answers and this gives the right one.
 */
export function LoadProblem({ error, noun }: { error: unknown; noun: string }) {
  const status = error instanceof ApiError ? error.status : null
  // ★ No error at all also means missing. A query that answered 200 with null,
  // or one that never ran because its route parameter was absent, is not
  // something to retry — telling somebody to try again on a record that does
  // not exist is how they end up entering it a second time.
  const missing = status === 404 || error === null || error === undefined
  return (
    <div className="p-4">
      <p className={`rounded-lg px-3 py-2.5 text-sm ${
        missing ? 'bg-neutral-50 text-neutral-700' : 'bg-warning-50 text-warning-800'}`}>
        {missing ? (
          <>This {noun} no longer exists.</>
        ) : status === 403 ? (
          <>You do not have access to this {noun}.</>
        ) : (
          <>
            This {noun} could not be loaded
            {error instanceof ApiError && error.message ? ` — ${error.message}` : ''}.
            It has not gone anywhere; try again.
          </>
        )}
      </p>
    </div>
  )
}
