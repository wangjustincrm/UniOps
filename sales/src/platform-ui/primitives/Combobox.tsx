import { useMemo, useState, type KeyboardEvent } from 'react'
import { Check, ChevronsUpDown } from 'lucide-react'
import { Popover } from './Popover'
import { cn } from '../lib/cn'

export interface ComboOption {
  value: string
  label: string
  hint?: string
}

export interface ComboboxProps {
  options: ComboOption[]
  value: string | null
  onChange: (value: string | null) => void
  placeholder?: string
  emptyText?: string
  disabled?: boolean
  id?: string
  className?: string
}

/** Searchable select (keyboard: ↑ ↓ Enter Escape). */
export function Combobox({
  options, value, onChange, placeholder = 'Select…', emptyText = 'No match', disabled, id, className,
}: ComboboxProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const selected = options.find((o) => o.value === value) ?? null
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? options.filter((o) => `${o.label} ${o.hint ?? ''}`.toLowerCase().includes(q)) : options
  }, [options, query])

  const pick = (o: ComboOption) => {
    onChange(o.value)
    setOpen(false)
    setQuery('')
  }

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, shown.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter' && shown[active]) { e.preventDefault(); pick(shown[active]) }
    else if (e.key === 'Escape') setOpen(false)
  }

  return (
    <Popover
      open={open}
      onOpenChange={(o) => { setOpen(o); setActive(0) }}
      className="w-[var(--radix-popover-trigger-width)] p-1"
      trigger={
        <button
          type="button"
          id={id}
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className={cn(
            'flex h-10 w-full items-center justify-between rounded-lg border border-neutral-200 bg-white px-3 text-left text-sm',
            'disabled:opacity-50',
            !selected && 'text-neutral-400',
            className,
          )}
        >
          <span className="truncate">{selected ? selected.label : placeholder}</span>
          <ChevronsUpDown className="h-4 w-4 shrink-0 text-neutral-400" />
        </button>
      }
    >
      <input
        autoFocus
        value={query}
        onChange={(e) => { setQuery(e.target.value); setActive(0) }}
        onKeyDown={onKey}
        placeholder="Search…"
        aria-label="Search options"
        className="mb-1 h-9 w-full rounded-md border border-neutral-200 px-2 text-sm outline-none focus:border-primary-600"
      />
      <ul role="listbox" className="max-h-64 overflow-y-auto">
        {shown.length === 0 && <li className="px-2 py-2 text-sm text-neutral-400">{emptyText}</li>}
        {shown.map((o, i) => (
          <li
            key={o.value}
            role="option"
            aria-selected={o.value === value}
            onMouseEnter={() => setActive(i)}
            onClick={() => pick(o)}
            className={cn(
              'flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm',
              i === active && 'bg-neutral-100',
            )}
          >
            <Check className={cn('h-4 w-4', o.value === value ? 'opacity-100' : 'opacity-0')} />
            <span className="truncate">{o.label}</span>
            {o.hint && <span className="ml-auto truncate text-xs text-neutral-400">{o.hint}</span>}
          </li>
        ))}
      </ul>
    </Popover>
  )
}
