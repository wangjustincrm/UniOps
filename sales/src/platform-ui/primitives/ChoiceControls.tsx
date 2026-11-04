import { useRef, type KeyboardEvent } from 'react'
import { cn } from '../lib/cn'

export interface Choice { value: string; label: string }

const focus = 'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary-600'

/** One of a few short choices, all in view (Test / Live): a radio group, one
 *  tab stop, the arrow keys move and choose. */
export function Segmented({ id, value, onChange, onBlur, options, disabled, 'aria-label': label, 'aria-invalid': invalid }: {
  id?: string; value: string | null; onChange: (v: string) => void; onBlur?: () => void; options: Choice[]; disabled?: boolean
  'aria-label'?: string; 'aria-invalid'?: boolean
}) {
  const box = useRef<HTMLDivElement>(null)
  const current = options.findIndex((o) => o.value === value)
  const keys = (e: KeyboardEvent) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0
    if (!step || disabled) return
    e.preventDefault()
    const next = (Math.max(current, 0) + step + options.length) % options.length
    onChange(options[next].value)
    box.current?.querySelectorAll<HTMLButtonElement>('[role=radio]')[next]?.focus()
  }
  return (
    <div ref={box} id={id} role="radiogroup" aria-label={label} aria-invalid={invalid} onKeyDown={keys} onBlur={onBlur}
      className="inline-flex max-w-full flex-wrap gap-0.5 rounded-lg border border-neutral-200 bg-neutral-50 p-0.5">
      {options.map((o, i) => (
        <button key={o.value} type="button" role="radio" aria-checked={o.value === value} disabled={disabled}
          tabIndex={i === Math.max(current, 0) ? 0 : -1} onClick={() => onChange(o.value)}
          className={cn('rounded-md px-3 py-1 text-sm disabled:opacity-50', focus,
            o.value === value ? 'bg-white font-medium text-primary-700 shadow-sm' : 'text-neutral-600 hover:text-neutral-800')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

const WEEK: Choice[] = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => ({ value: d, label: d }))

/** Days of the week as chips, any number of them on (an array of values, in
 *  the options' order whatever the clicks' order). */
export function DayChips({ values, onChange, options = WEEK, disabled, 'aria-label': label }: {
  values: string[]; onChange: (v: string[]) => void; options?: Choice[]; disabled?: boolean; 'aria-label'?: string
}) {
  const toggle = (v: string) => {
    const on = values.includes(v) ? values.filter((x) => x !== v) : [...values, v]
    onChange(options.map((o) => o.value).filter((x) => on.includes(x)))
  }
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1">
      {options.map((o) => {
        const on = values.includes(o.value)
        return (
          <button key={o.value} type="button" aria-pressed={on} disabled={disabled} onClick={() => toggle(o.value)}
            className={cn('h-8 min-w-11 rounded-full border px-2 text-xs font-medium disabled:opacity-50', focus,
              on ? 'border-primary-600 bg-primary-50 text-primary-800' : 'border-neutral-200 bg-white text-neutral-600 hover:border-neutral-300')}>
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
