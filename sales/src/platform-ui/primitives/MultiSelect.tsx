import { useId, useState, type KeyboardEvent } from 'react'
import { Check, ChevronsUpDown } from 'lucide-react'
import { cn } from '../lib/cn'
import { Popover } from './Popover'
import { CHOOSING } from './submitOnEnter'

export interface MultiOption {
  value: string
  label: string
  hint?: string
}

export interface MultiSelectProps {
  options: MultiOption[]
  values: string[]
  onChange: (values: string[]) => void
  /** The field's accessible name. */
  label: string
  placeholder?: string
  disabled?: boolean
  className?: string
}

/** Several values from a long list: type to narrow it. Keys: ↑ ↓ move, Enter chooses and closes,
 *  Space chooses and stays open (with nothing typed), Escape closes. A click chooses and stays open. */
export function MultiSelect({ options, values, onChange, label, placeholder = 'Any', disabled, className }: MultiSelectProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(-1)  // nothing is chosen by Enter until a row is reached
  const listId = `${useId()}-list`
  const q = query.trim().toLowerCase()
  const shown = options.filter((o) => !q || `${o.label} ${o.hint ?? ''}`.toLowerCase().includes(q))
  const toggle = (v: string) => onChange(values.includes(v) ? values.filter((x) => x !== v) : [...values, v])
  const reset = (next: boolean) => {
    setOpen(next)
    setQuery('')
    setActive(-1)
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, shown.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter') {
      e.preventDefault()
      const row = shown[active >= 0 ? active : q ? 0 : -1]
      if (row) toggle(row.value)
      reset(false)
    } else if (e.key === ' ' && !query && shown[active]) { e.preventDefault(); toggle(shown[active].value) }
  }
  const names = options.filter((o) => values.includes(o.value)).map((o) => o.label)
  return (
    <Popover
      open={open}
      onOpenChange={reset}
      className="w-[var(--radix-popover-trigger-width)] min-w-[14rem] p-1"
      trigger={
        <button type="button" role="combobox" aria-expanded={open} aria-controls={listId} aria-label={label} disabled={disabled}
          className={cn('flex h-10 w-full items-center justify-between gap-2 rounded-lg border border-neutral-200 bg-white px-3 text-left text-sm',
            'disabled:cursor-not-allowed disabled:opacity-50', names.length === 0 && 'text-neutral-400', className)}>
          <span className="truncate">{names.length === 0 ? placeholder : names.length <= 2 ? names.join(', ') : `${names[0]} +${names.length - 1}`}</span>
          <ChevronsUpDown className="h-4 w-4 shrink-0 text-neutral-400" />
        </button>
      }
    >
      <input {...CHOOSING} autoFocus value={query} aria-label={`${label}: search`} aria-controls={listId} placeholder="Type to search…"
        onChange={(e) => { setQuery(e.target.value); setActive(e.target.value ? 0 : -1) }} onKeyDown={onKey}
        className="mb-1 h-9 w-full rounded-md border border-neutral-200 px-2 text-sm outline-none focus:border-primary-600" />
      <ul id={listId} role="listbox" aria-multiselectable aria-label={label} className="max-h-64 overflow-y-auto">
        {shown.length === 0 && <li className="px-2 py-2 text-sm text-neutral-400">No match</li>}
        {shown.map((o, i) => {
          const on = values.includes(o.value)
          return (
            <li key={o.value} role="option" aria-selected={on} onMouseEnter={() => setActive(i)} onClick={() => toggle(o.value)}
              className={cn('flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm', i === active && 'bg-neutral-100')}>
              <span aria-hidden className={cn('flex h-4 w-4 shrink-0 items-center justify-center rounded border',
                on ? 'border-primary-600 bg-primary-600 text-white' : 'border-neutral-300 bg-white')}>
                {on && <Check className="h-3 w-3" />}
              </span>
              <span className="truncate">{o.label}</span>
              {o.hint && <span className="ml-auto truncate text-xs text-neutral-400">{o.hint}</span>}
            </li>
          )
        })}
      </ul>
    </Popover>
  )
}
