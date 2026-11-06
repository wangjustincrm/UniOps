import { useId, useRef, useState, type KeyboardEvent } from 'react'
import * as RP from '@radix-ui/react-popover'
import { cn } from '../lib/cn'
import { Chip } from './Chip'

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

/** Several values from a list: what is chosen sits as tags in the box, and the box is where you type
 *  to narrow the list below it, which offers only what is not chosen yet. Keys: ↑ ↓ open and move, Enter
 *  chooses and closes (Enter on the closed box goes to the form), Backspace removes the last tag, Escape
 *  closes. A click chooses and leaves the list open for the next one. */
export function MultiSelect({ options, values, onChange, label, placeholder = 'Select…', disabled, className }: MultiSelectProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const id = useId()
  const listId = `${id}-list`
  const q = query.trim().toLowerCase()
  const shown = options.filter((o) => !values.includes(o.value) && (!q || `${o.label} ${o.hint ?? ''}`.toLowerCase().includes(q)))
  const row = Math.min(active, shown.length - 1)
  const show = () => { if (!disabled) setOpen(true) }
  const close = () => { setOpen(false); setQuery(''); setActive(0) }
  const add = (v: string) => { onChange([...values, v]); setQuery(''); setActive(0) }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); if (open) setActive(Math.min(row + 1, shown.length - 1)); else show() }
    else if (e.key === 'ArrowUp') { e.preventDefault(); if (open) setActive(Math.max(row - 1, 0)); else show() }
    else if (e.key === 'Enter' && open) {
      e.preventDefault()
      if (shown[row]) add(shown[row].value)
      close()
    } else if (e.key === 'Escape' && open) { e.preventDefault(); e.stopPropagation(); close() }
    else if (e.key === 'Backspace' && !query && values.length) onChange(values.slice(0, -1))
  }
  const name = (v: string) => options.find((o) => o.value === v)?.label ?? v
  return (
    <RP.Root open={open}>
      <RP.Anchor asChild>
        <div ref={box} onClick={(e) => { input.current?.focus(); if (!(e.target as HTMLElement).closest('button')) show() }}
          className={cn('flex min-h-10 w-full cursor-text flex-wrap items-center gap-1 rounded-lg border border-neutral-200 bg-white px-2 py-1 text-sm',
            'focus-within:border-primary-600 focus-within:ring-1 focus-within:ring-primary-600', disabled && 'cursor-not-allowed opacity-50', className)}>
          {values.map((v) => (
            <Chip key={v} removeLabel={`Remove ${name(v)}`} onRemove={() => onChange(values.filter((x) => x !== v))}>{name(v)}</Chip>
          ))}
          <input ref={input} role="combobox" aria-label={label} aria-expanded={open} aria-controls={listId} aria-autocomplete="list"
            aria-activedescendant={open && shown[row] ? `${id}-${row}` : undefined} disabled={disabled} value={query}
            placeholder={values.length ? '' : placeholder} onKeyDown={onKey}
            onChange={(e) => { setQuery(e.target.value); setActive(0); show() }}
            className="h-7 min-w-[5rem] flex-1 bg-transparent px-1 outline-none placeholder:text-neutral-400" />
        </div>
      </RP.Anchor>
      <RP.Portal>
        <RP.Content align="start" sideOffset={4} onOpenAutoFocus={(e) => e.preventDefault()} onCloseAutoFocus={(e) => e.preventDefault()}
          onInteractOutside={(e) => { if (box.current?.contains(e.target as Node)) e.preventDefault(); else close() }}
          onEscapeKeyDown={(e) => { e.preventDefault(); close() }}
          className="z-50 w-[var(--radix-popover-trigger-width)] min-w-[12rem] rounded-lg border border-neutral-200 bg-white p-1 shadow-lg focus:outline-none">
          <ul id={listId} role="listbox" aria-multiselectable aria-label={label} className="max-h-64 overflow-y-auto">
            {shown.length === 0 && <li className="px-2 py-2 text-sm text-neutral-400">{q ? 'No match' : 'All chosen'}</li>}
            {shown.map((o, i) => (
              <li key={o.value} id={`${id}-${i}`} role="option" aria-selected={i === row} onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => e.preventDefault()} onClick={() => add(o.value)}
                className={cn('flex cursor-pointer items-center gap-2 rounded-md px-3 py-1.5 text-sm', i === row && 'bg-primary-50')}>
                <span className="truncate">{o.label}</span>
                {o.hint && <span className="ml-auto truncate text-xs text-neutral-400">{o.hint}</span>}
              </li>
            ))}
          </ul>
        </RP.Content>
      </RP.Portal>
    </RP.Root>
  )
}
