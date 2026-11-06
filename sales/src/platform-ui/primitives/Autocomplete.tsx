import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Check, ChevronsUpDown } from 'lucide-react'
import { Popover } from './Popover'
import { CHOOSING } from './submitOnEnter'
import type { ComboOption } from './Combobox'
import { cn } from '../lib/cn'

export interface AutocompleteProps {
  value: string | null
  onChange: (value: string | null, option?: ComboOption) => void
  /** Options for what was typed — e.g. a list endpoint's ?search=. */
  loadOptions: (query: string) => Promise<ComboOption[]>
  /** The label of the current value, when it is not just the value itself. */
  selectedLabel?: string
  minChars?: number
  debounceMs?: number
  placeholder?: string
  emptyText?: string
  id?: string
  disabled?: boolean
  'aria-label'?: string
  'aria-invalid'?: boolean
  onBlur?: () => void
  className?: string
}

/** A searchable select whose options come from a query (type to search;
 *  ↑ ↓ Enter Escape). Opened with nothing typed it shows the first page (the
 *  loader's answer to ""); `minChars` applies to typed text only. Waits
 *  `debounceMs` after typing; an answer to an older query is dropped. The
 *  chosen value shows as "label — hint" (e.g. "CF0092 — Formula Stage 1"); a
 *  value set from outside is looked up once (by its value, else in the first
 *  page) to show the same. */
export function Autocomplete({
  value, onChange, loadOptions, selectedLabel, minChars = 1, debounceMs = 250, placeholder = 'Search…',
  emptyText = 'No match', id, disabled, onBlur, className, ...aria
}: AutocompleteProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  // The answer and the query it answers: until it matches what is typed, the list says Searching….
  const [state, setState] = useState<{ query: string | null; options: ComboOption[]; error: boolean }>({ query: null, options: [], error: false })
  const [active, setActive] = useState(0)
  const latest = useRef(0)
  const load = useRef(loadOptions)
  useEffect(() => { load.current = loadOptions }, [loadOptions])

  const q = query.trim()
  const enough = q.length === 0 || q.length >= minChars
  // The option behind the value, to show its label and hint.
  const [known, setKnown] = useState<ComboOption | null>(null)
  useEffect(() => {
    if (!value || known?.value === value || selectedLabel) return
    let live = true
    const find = (opts: ComboOption[]) => opts.find((o) => o.value === value)
    load.current(value)
      .then((opts) => find(opts) ?? load.current('').then(find))
      .then((o) => { if (live && o) setKnown(o) }, () => {})
    return () => { live = false }
  }, [value, known, selectedLabel])
  const shownLabel = selectedLabel ?? (known && known.value === value ? (known.hint ? `${known.label} — ${known.hint}` : known.label) : value)
  useEffect(() => {
    if (!open || !enough) return
    const ticket = ++latest.current
    const timer = setTimeout(() => {
      load.current(q).then(
        (options) => { if (ticket === latest.current) { setState({ query: q, options, error: false }); setActive(0) } },
        () => { if (ticket === latest.current) setState({ query: q, options: [], error: true }) },
      )
    }, debounceMs)
    return () => clearTimeout(timer)
  }, [q, open, enough, debounceMs])

  const loading = enough && state.query !== q
  const pick = (o: ComboOption) => {
    setKnown(o)
    onChange(o.value, o)
    setOpen(false)
    setQuery('')
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    const shown = enough && !loading ? state.options : []
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, shown.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter' && shown[active]) { e.preventDefault(); pick(shown[active]) }
    else if (e.key === 'Escape') setOpen(false)
  }
  const shown = enough && !loading ? state.options : []
  const listId = `${useId()}-list`
  return (
    <Popover
      open={open}
      onOpenChange={(o) => { setOpen(o); if (!o) onBlur?.() }}
      className="w-[var(--radix-popover-trigger-width)] min-w-[14rem] p-1"
      trigger={
        <button type="button" id={id} role="combobox" aria-expanded={open} aria-controls={listId} disabled={disabled} {...aria}
          className={cn(
            'flex h-10 w-full items-center justify-between rounded-lg border border-neutral-200 bg-white px-3 text-left text-sm',
            'disabled:cursor-not-allowed disabled:opacity-50', !value && 'text-neutral-400',
            aria['aria-invalid'] && 'border-danger-600 bg-danger-50', className,
          )}>
          <span className="truncate">{value ? shownLabel : placeholder}</span>
          <ChevronsUpDown className="h-4 w-4 shrink-0 text-neutral-400" />
        </button>
      }
    >
      <input {...CHOOSING} autoFocus value={query} onKeyDown={onKey} aria-label={`${aria['aria-label'] ?? 'Options'}: search`}
        aria-controls={listId} placeholder={minChars > 1 ? `Type ${minChars} or more characters…` : 'Type to search…'}
        onChange={(e) => setQuery(e.target.value)}
        className="mb-1 h-9 w-full rounded-md border border-neutral-200 px-2 text-sm outline-none focus:border-primary-600" />
      <ul id={listId} role="listbox" className="max-h-64 overflow-y-auto">
        {!enough && <li className="px-2 py-2 text-sm text-neutral-400">Type at least {minChars} character{minChars === 1 ? '' : 's'}</li>}
        {q.length === 0 && !loading && shown.length > 0 && <li aria-hidden className="px-2 py-1 text-xs text-neutral-400">Type to search…</li>}
        {enough && loading && <li className="px-2 py-2 text-sm text-neutral-400">Searching…</li>}
        {enough && !loading && state.error && <li className="px-2 py-2 text-sm text-danger-600">Could not load the options</li>}
        {enough && !loading && !state.error && shown.length === 0 && <li className="px-2 py-2 text-sm text-neutral-400">{emptyText}</li>}
        {!loading && shown.map((o, i) => (
          <li key={o.value} role="option" aria-selected={o.value === value} onMouseEnter={() => setActive(i)} onClick={() => pick(o)}
            className={cn('flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm', i === active && 'bg-neutral-100')}>
            <Check className={cn('h-4 w-4', o.value === value ? 'opacity-100' : 'opacity-0')} />
            <span className="truncate">{o.label}</span>
            {o.hint && <span className="ml-auto truncate text-xs text-neutral-400">{o.hint}</span>}
          </li>
        ))}
      </ul>
    </Popover>
  )
}
