// Text input with a pick list (WAI-ARIA combobox, list autocomplete).
//
// The caller owns everything about the data: the input text, the options
// (usually a server query keyed on a debounced copy of the text) and what a
// pick means. This component owns only open/closed, the active option and
// the keyboard: ↑/↓ move, Enter picks (or hands the typed text to
// onEnterText when nothing is active), Esc closes, a click outside closes.
//
// The list renders in a portal on <body> with fixed positioning — the pickers
// sit above scrolling tables and inside overflow boxes, either of which
// would clip or cover an in-flow dropdown (repo rule: overlays use a portal).
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, Loader2, X } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ComboboxProps<T> {
  /** The input's text (controlled). */
  inputValue: string
  onInputChange: (text: string) => void
  options: readonly T[]
  getKey: (option: T) => string
  renderOption: (option: T, state: { active: boolean; selected: boolean }) => ReactNode
  onSelect: (option: T) => void
  /** Enter with no active option (e.g. a typed-in complete code). */
  onEnterText?: (text: string) => void
  /** Key of the currently chosen option, for aria-selected and the tick. */
  selectedKey?: string | null
  /** Told when the list opens/closes — callers fetch options only while open. */
  onOpenChange?: (open: boolean) => void
  /** Shows a clear (×) button when the input has text. */
  onClear?: () => void
  loading?: boolean
  /** Shown instead of options (e.g. a failed request). */
  errorText?: string | null
  emptyText?: string
  /** A note under the options (e.g. "Showing 500 of 812"). */
  footer?: ReactNode
  disabled?: boolean
  placeholder?: string
  autoFocus?: boolean
  /** Accessible name when there is no wrapping <label>. */
  ariaLabel?: string
  id?: string
  className?: string
  inputClassName?: string
  /** Minimum list width in px (the list is at least as wide as the input). */
  listMinWidth?: number
}

interface Pos { left: number; top: number; width: number; maxHeight: number; above: boolean }

export function Combobox<T>({
  inputValue, onInputChange, options, getKey, renderOption, onSelect, onEnterText, selectedKey,
  onOpenChange, onClear, loading, errorText, emptyText = 'No matches', footer, disabled, placeholder,
  autoFocus, ariaLabel, id, className, inputClassName, listMinWidth = 320,
}: ComboboxProps<T>) {
  const autoId = useId()
  const inputId = id ?? `cb-${autoId}`
  const listId = `${inputId}-list`
  const optionId = (i: number) => `${inputId}-opt-${i}`

  const [open, setOpenState] = useState(false)
  const [active, setActive] = useState(-1)
  const [pos, setPos] = useState<Pos | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const setOpen = useCallback((next: boolean) => {
    setOpenState(next)
    if (!next) setActive(-1)
  }, [])

  // A disabled box never shows its list (adjusted during render so the two
  // states never disagree on screen).
  if (disabled && open) {
    setOpenState(false)
    setActive(-1)
  }

  const onOpenChangeRef = useRef(onOpenChange)
  useEffect(() => { onOpenChangeRef.current = onOpenChange })
  useEffect(() => { onOpenChangeRef.current?.(open) }, [open])

  // New options (a new search) invalidate the active index.
  const [seenOptions, setSeenOptions] = useState(options)
  if (seenOptions !== options) {
    setSeenOptions(options)
    setActive(options.length > 0 && active >= 0 ? 0 : -1)
  }

  const place = useCallback(() => {
    const el = wrapRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const vh = window.innerHeight
    const below = vh - r.bottom - 8
    const aboveSpace = r.top - 8
    const above = below < 200 && aboveSpace > below
    const width = Math.min(Math.max(r.width, listMinWidth), window.innerWidth - 16)
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8))
    setPos({
      left, width, above,
      top: above ? r.top - 4 : r.bottom + 4,
      maxHeight: Math.max(120, Math.min(360, above ? aboveSpace : below)),
    })
  }, [listMinWidth])

  useLayoutEffect(() => {
    if (!open) return
    place()
    // Capture phase: any scrolling ancestor (the tab page, a table) moves the input.
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open, place])

  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      const t = e.target as Node
      if (wrapRef.current?.contains(t) || listRef.current?.contains(t)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open, setOpen])

  useEffect(() => {
    if (!open || active < 0) return
    document.getElementById(optionId(active))?.scrollIntoView({ block: 'nearest' })
    // optionId is derived from inputId only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, active])

  function pick(i: number) {
    const o = options[i]
    if (o === undefined) return
    onSelect(o)
    setOpen(false)
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (disabled) return
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        if (!open) { setOpen(true); setActive(options.length ? 0 : -1); return }
        setActive((a) => (options.length ? (a + 1) % options.length : -1))
        return
      case 'ArrowUp':
        e.preventDefault()
        if (!open) { setOpen(true); setActive(options.length ? options.length - 1 : -1); return }
        setActive((a) => (options.length ? (a <= 0 ? options.length - 1 : a - 1) : -1))
        return
      case 'Enter':
        e.preventDefault() // never submit the surrounding form from here
        if (open && active >= 0) pick(active)
        else onEnterText?.(inputValue)
        return
      case 'Escape':
        if (open) { e.preventDefault(); e.stopPropagation(); setOpen(false) }
        return
      case 'Tab':
        setOpen(false)
        return
    }
  }

  const list = open && pos && createPortal(
    <div
      style={{
        position: 'fixed', left: pos.left, width: pos.width,
        ...(pos.above ? { bottom: window.innerHeight - pos.top } : { top: pos.top }),
      }}
      className="z-[80] overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-lg">
      <ul ref={listRef} id={listId} role="listbox" aria-labelledby={inputId}
        style={{ maxHeight: pos.maxHeight }} className="overflow-y-auto py-1 text-sm">
        {errorText ? (
          <li role="presentation" className="px-3 py-2 text-xs text-danger-700">{errorText}</li>
        ) : options.length === 0 ? (
          <li role="presentation" className="px-3 py-2 text-xs text-neutral-400">
            {loading ? 'Loading…' : emptyText}
          </li>
        ) : options.map((o, i) => {
          const key = getKey(o)
          const selected = selectedKey != null && key === selectedKey
          return (
            <li key={key} id={optionId(i)} role="option" aria-selected={selected}
              onMouseDown={(e) => e.preventDefault() /* keep focus in the input */}
              onMouseMove={() => { if (active !== i) setActive(i) }}
              onClick={() => pick(i)}
              className={cn('cursor-pointer px-3 py-1.5', i === active ? 'bg-primary-50' : 'hover:bg-neutral-50',
                selected && 'font-medium')}>
              {renderOption(o, { active: i === active, selected })}
            </li>
          )
        })}
      </ul>
      {footer && !errorText && (
        <div className="border-t border-neutral-100 px-3 py-1.5 text-[11px] text-neutral-500">{footer}</div>
      )}
    </div>,
    document.body,
  )

  return (
    <div ref={wrapRef} className={cn('relative', className)}>
      <input
        ref={inputRef}
        id={inputId}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && active >= 0 ? optionId(active) : undefined}
        aria-label={ariaLabel}
        autoComplete="off"
        spellCheck={false}
        value={inputValue}
        disabled={disabled}
        placeholder={placeholder}
        autoFocus={autoFocus}
        onChange={(e) => { onInputChange(e.target.value); if (!open) setOpen(true) }}
        onFocus={() => { if (!disabled) setOpen(true) }}
        onClick={() => { if (!disabled && !open) setOpen(true) }}
        onKeyDown={onKeyDown}
        className={cn(
          'h-9 w-full rounded-lg border border-neutral-300 bg-white pl-2 pr-12 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:cursor-not-allowed disabled:bg-neutral-100 disabled:text-neutral-400',
          inputClassName)}
      />
      <span className="pointer-events-none absolute inset-y-0 right-1.5 flex items-center gap-0.5 text-neutral-400">
        {loading && open && <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />}
        {onClear && inputValue && !disabled && (
          <button type="button" aria-label="Clear" tabIndex={-1}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => { onClear(); inputRef.current?.focus() }}
            className="pointer-events-auto rounded p-0.5 hover:text-neutral-700">
            <X className="h-3.5 w-3.5" />
          </button>
        )}
        <button type="button" aria-label={open ? 'Close list' : 'Open list'} tabIndex={-1} disabled={disabled}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => { setOpen(!open); inputRef.current?.focus() }}
          className="pointer-events-auto rounded p-0.5 hover:text-neutral-700 disabled:cursor-not-allowed">
          <ChevronDown className="h-4 w-4" />
        </button>
      </span>
      {list}
    </div>
  )
}
