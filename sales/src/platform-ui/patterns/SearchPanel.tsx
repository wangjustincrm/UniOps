import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, ChevronRight, Plus, Search as SearchIcon, SlidersHorizontal, X } from 'lucide-react'
import { cn } from '../lib/cn'
import { Select } from '../primitives/Select'
import { CHOOSING, submitOnEnter } from '../primitives/submitOnEnter'
import { Button, Input } from '../primitives/shell'
import { Hint } from '../primitives/Text'
import type { FieldMeta } from '../resource/listMeta'
import type { Filter } from '../resource/querySpec'
import { TourMark } from '../help'
import { opLabel, panelOps } from './listView'
import { cleared, draftFrom, draftOf, filterFrom, type Draft, type DraftOp, type PanelDraft } from './searchDraft'
import { ValueInput } from './ValueInput'

const OTHER = 'Other'
const COUNT_PAUSE_MS = 400

export interface SearchPanelProps {
  open: boolean
  /** Closed: `kept` (a record sheet opened over it) keeps the draft; otherwise focus goes back to the icon. */
  onOpenChange: (open: boolean, how?: 'kept') => void
  /** The filterable fields the user may set (locked ones left out). */
  fields: FieldMeta[]
  /** The current filters of those fields. */
  filters: Filter[]
  /** The draft while the panel is closed by a record sheet; null: start from `filters`. */
  draft: PanelDraft | null
  onDraftChange: (d: PanelDraft | null) => void
  /** Opened on a field ("All fields…" of a column's search): that field in use, its value focused. */
  focusField?: string | null
  /** Every filter of these fields, replacing the current ones. */
  onApply: (filters: Filter[]) => void
  suggest?: (field: string, query: string) => Promise<string[]>
  /** The list's groups in order (/meta), and the fields shown as columns (first in their group, "in the table"). */
  groups?: string[]
  shown?: string[]
  /** How many rows these filters would give (the list's count alone); absent: the button says Search only. */
  count?: (filters: Filter[]) => Promise<number>
  /** The view's own filters of these fields: "Back to the view" returns to them. */
  viewFilters?: Filter[] | null
}

/** Search on any field (#32 b, the user's design 2026-11-26): the toolbar icon opens a panel on the right (the table stays in
 *  sight; a phone gets the whole screen). In use on top, each with ✕; a cleared one stays struck through until ✕; then the other
 *  fields in their groups (one level, the shown columns first), Find a field, Clear all, Back to the view and Search (n). Tab
 *  stays inside while it is open, Esc closes, focus goes back to the icon. */
export function SearchPanel(props: SearchPanelProps) {
  const { open, onOpenChange, filters } = props
  const icon = useRef<HTMLButtonElement>(null)
  const active = new Set(filters.map((f) => f.field)).size
  const label = 'Search on any field'
  const close = (how?: 'kept') => {
    onOpenChange(false, how)
    if (!how) requestAnimationFrame(() => icon.current?.focus())
  }
  return (
    <>
      <TourMark help="list.searchPanel" slot="list.searchPanel" also={{ slot: 'list.statusFind', text: 'list.statusFind' }} className="inline-flex">
        <button ref={icon} type="button" aria-label={active ? `${label} (${active})` : label} title={label} aria-expanded={open}
          onClick={() => (open ? close() : onOpenChange(true))}
          className={cn('relative inline-flex h-8 w-8 items-center justify-center rounded-lg border border-neutral-200 bg-white text-neutral-600',
            'hover:bg-neutral-50 hover:text-neutral-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600',
            open && 'bg-neutral-100', active ? 'border-primary-200 text-primary-700' : '')}>
          <SlidersHorizontal className="h-4 w-4" />
          {!!active && (
            <span aria-hidden className="absolute -right-1.5 -top-1.5 min-w-[1rem] rounded-full bg-primary-600 px-1 text-center text-[10px] font-semibold leading-4 text-white">{active}</span>
          )}
        </button>
      </TourMark>
      {open && createPortal(<Panel {...props} close={close} />, document.body)}
    </>
  )
}

function Panel({ fields, filters, draft, onDraftChange, focusField, onApply, suggest, groups = [], shown = [], count, viewFilters, close }:
  SearchPanelProps & { close: (how?: 'kept') => void }) {
  const box = useRef<HTMLDivElement>(null)
  const [focusOn, setFocusOn] = useState<string | null>(focusField ?? null)
  // The draft: kept by the list (a record sheet closes the panel and keeps it), else made from the current filters.
  const start = useMemo(() => {
    const d = draft ?? draftOf(fields, filters)
    if (!focusField || d.rows.includes(focusField)) return d
    const f = fields.find((x) => x.name === focusField)
    return f ? { ...d, rows: [...d.rows, f.name], drafts: { ...d.drafts, [f.name]: { ...draftFrom(f, []), on: true } } } : d
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps -- the draft as it was when the panel opened
  // The first group starts open (with one group, it is always open).
  const [d, setD] = useState<PanelDraft>(() => (start.openGroups.length || !groups.length ? start : { ...start, openGroups: [groups[0]] }))
  useEffect(() => { onDraftChange(d) }, [d])  // eslint-disable-line react-hooks/exhaustive-deps -- the list keeps it
  const byName = useMemo(() => new Map(fields.map((f) => [f.name, f])), [fields])
  const applied = useMemo(() => new Set(filters.map((f) => f.field)), [filters])
  const result = (x: PanelDraft) => x.rows.map((n) => (byName.get(n) ? filterFrom(byName.get(n)!, { ...x.drafts[n], on: true }) : null))
    .filter((f): f is Filter => f !== null)
  const next = result(d)
  const key = JSON.stringify(next)

  // Search (n): counted a short pause after the last change; an answer for an older draft is dropped.
  const [counted, setCounted] = useState<{ key: string; n: number } | null>(null)
  const asked = useRef(0)
  useEffect(() => {
    if (!count) return
    const mine = ++asked.current
    const t = setTimeout(() => {
      count(next).then((n) => { if (asked.current === mine) setCounted({ key, n }) }, () => { if (asked.current === mine) setCounted(null) })
    }, COUNT_PAUSE_MS)
    return () => clearTimeout(t)
  }, [key])  // eslint-disable-line react-hooks/exhaustive-deps -- `next` is `key`

  useLayoutEffect(() => {  // focus: the field asked for, else the first thing in the panel
    const target = focusOn ? box.current?.querySelector<HTMLElement>(`[data-row="${CSS.escape(focusOn)}"] input, [data-row="${CSS.escape(focusOn)}"] button[role="combobox"]`) : null
    ;(target ?? box.current?.querySelector<HTMLElement>('input'))?.focus()
  }, [focusOn])

  const setDraft = (name: string, p: Partial<Draft>) => setD((x) => ({ ...x, drafts: { ...x.drafts, [name]: { ...x.drafts[name], ...p } } }))
  const add = (f: FieldMeta) => {
    setD((x) => ({ ...x, rows: x.rows.includes(f.name) ? x.rows : [...x.rows, f.name],
      drafts: { ...x.drafts, [f.name]: x.drafts[f.name] ?? { ...draftFrom(f, []), on: true } } }))
    setFocusOn(f.name)
  }
  const remove = (name: string) => setD((x) => {
    const drafts = { ...x.drafts }
    delete drafts[name]
    return { ...x, rows: x.rows.filter((n) => n !== name), drafts }
  })
  const search = () => { onApply(next); onDraftChange(null); close() }
  const backToView = viewFilters ? () => setD(draftOf(fields, viewFilters)) : undefined
  const sameAsView = !!viewFilters && JSON.stringify(result(draftOf(fields, viewFilters))) === key

  const trap = (e: KeyboardEvent<HTMLDivElement>) => {
    // Esc closes the panel, unless it closes a list that is open (a choice's options): then the next Esc does.
    if (e.key === 'Escape' && (e.target as HTMLElement).getAttribute('aria-expanded') !== 'true' && !document.querySelector('[role="listbox"]')) {
      e.preventDefault()
      close()
      return
    }
    if (e.key !== 'Tab' || !box.current) return
    const all = [...box.current.querySelectorAll<HTMLElement>('input, button, select, textarea, [tabindex]:not([tabindex="-1"])')]
      .filter((el) => !el.hasAttribute('disabled') && !el.closest('[hidden], [aria-hidden="true"]'))
    if (all.length === 0) return
    const [first, last] = [all[0], all[all.length - 1]]
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
  }

  const find = d.find.trim().toLowerCase()
  const order = [...groups.filter((g) => g !== OTHER), OTHER]
  const free = fields.filter((f) => !d.rows.includes(f.name) && (!find || f.label.toLowerCase().includes(find)))
  const shownAt = (n: string) => (shown.includes(n) ? shown.indexOf(n) : Infinity)
  const sections = order.map((g) => ({
    g, items: free.filter((f) => (groups.includes(f.group ?? OTHER) ? f.group ?? OTHER : OTHER) === g)
      .sort((a, b) => shownAt(a.name) - shownAt(b.name)),
  })).filter((s) => s.items.length > 0)
  const inUse = d.rows.filter((n) => byName.has(n))
  const counting = !!count && counted?.key !== key
  return (
    <div ref={box} role="dialog" aria-modal="false" aria-label="Search on any field" onKeyDown={trap}
      className="fixed inset-0 z-40 flex flex-col bg-white shadow-2xl sm:inset-y-0 sm:left-auto sm:right-0 sm:w-96 sm:border-l sm:border-neutral-200">
      <div className="flex items-center justify-between border-b border-neutral-100 px-4 py-3">
        <h2 className="text-base font-semibold text-neutral-900">Search</h2>
        <button type="button" aria-label="Close" onClick={() => close()}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-neutral-500 hover:bg-neutral-100 hover:text-neutral-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
          <X className="h-4 w-4" />
        </button>
      </div>
      <form className="flex min-h-0 flex-1 flex-col" onKeyDownCapture={submitOnEnter} onSubmit={(e) => { e.preventDefault(); search() }}>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
          <label className="relative my-2 block">
            <SearchIcon aria-hidden className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-neutral-400" />
            <Input {...(find ? CHOOSING : {})} aria-label="Find a field" placeholder="Find a field" value={d.find} className="pl-8"
              onKeyDown={(e) => { if (e.key === 'Enter' && find && sections.length) { e.preventDefault(); add(sections[0].items[0]); setD((x) => ({ ...x, find: '' })) } }}
              onChange={(e) => setD((x) => ({ ...x, find: e.target.value }))} />
          </label>
          {inUse.length > 0 && (
            <section aria-label="In use" className="border-b border-neutral-100 pb-2">
              <h3 className="py-1 text-xs font-semibold uppercase tracking-wide text-neutral-600">
                In use <span className="font-normal normal-case tracking-normal text-neutral-500">({inUse.filter((n) => filterFrom(byName.get(n)!, { ...d.drafts[n], on: true })).length})</span>
              </h3>
              <ul className="flex flex-col divide-y divide-dashed divide-neutral-100">
                {inUse.map((n) => {
                  const f = byName.get(n)!
                  const dr = { ...d.drafts[n], on: true }
                  const ops = panelOps(f)
                  const isCleared = applied.has(n) && !filterFrom(f, dr)
                  return (
                    <li key={n} data-row={n} className="flex flex-col gap-2 py-2">
                      <div className="flex items-center gap-2">
                        <span className={cn('flex-1 text-sm font-medium', isCleared ? 'text-neutral-500 line-through' : 'text-neutral-800')}>{f.label}</span>
                        <button type="button" aria-label={`Remove ${f.label}`} onClick={() => remove(n)}
                          className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-neutral-200 text-neutral-500 hover:bg-neutral-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                          <X className="h-3 w-3" />
                        </button>
                      </div>
                      {isCleared && <Hint as="p">Cleared: Search takes it off the list. ✕ takes the row away.</Hint>}
                      <div className="flex flex-wrap gap-2">
                        {ops.length > 1 && (
                          <Select aria-label={`${f.label}: how to compare`} value={dr.op}
                            onChange={(op) => setDraft(n, { op: (op ?? ops[0]) as DraftOp, ...(op === 'within' || dr.op === 'within' ? { values: [] } : {}) })}
                            options={ops.map((op) => ({ value: op, label: opLabel(op, f.type) }))} />
                        )}
                        <div className="min-w-[12rem] flex-1">
                          <ValueInput field={f} draft={dr} set={(p) => setDraft(n, p)} autoFocus={false} suggest={suggest} />
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            </section>
          )}
          {sections.map(({ g, items }) => {
            const isOpen = !!find || sections.length === 1 || d.openGroups.includes(g)
            return (
              <section key={g} aria-label={g} className="border-b border-neutral-100 py-1">
                <button type="button" aria-expanded={isOpen}
                  onClick={() => setD((x) => ({ ...x, openGroups: isOpen ? x.openGroups.filter((y) => y !== g) : [...x.openGroups, g] }))}
                  className="flex w-full items-center gap-1.5 rounded py-1 text-left text-xs font-semibold uppercase tracking-wide text-neutral-600 hover:text-neutral-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                  {isOpen ? <ChevronDown aria-hidden className="h-3.5 w-3.5" /> : <ChevronRight aria-hidden className="h-3.5 w-3.5" />}
                  {g} <span className="font-normal normal-case tracking-normal text-neutral-500">({items.length})</span>
                </button>
                {isOpen && (
                  <ul className="flex flex-col">
                    {items.map((f) => (
                      <li key={f.name}>
                        <button type="button" aria-label={`Add ${f.label}`} onClick={() => add(f)}
                          className="flex w-full items-center gap-2 rounded px-1 py-1.5 text-left text-sm text-neutral-800 hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                          <Plus aria-hidden className="h-3.5 w-3.5 text-primary-600" />
                          <span className="flex-1"><Marked text={f.label} find={find} /></span>
                          {shown.includes(f.name) && <span className="rounded-full border border-neutral-200 px-1.5 text-[11px] text-neutral-500">in the table</span>}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )
          })}
          {find && sections.length === 0 && <Hint as="p" className="py-2">No field matches “{d.find}”.</Hint>}
        </div>
        <div className="flex flex-col gap-2 border-t border-neutral-100 bg-white px-4 py-3">
          <Hint as="p">Enter adds · Enter again searches · Ctrl+Enter searches now · Esc closes</Hint>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" className="mr-auto"
              onClick={() => setD((x) => ({ ...x, drafts: Object.fromEntries(Object.entries(x.drafts).map(([k, v]) => [k, cleared(v, true)])) }))}>Clear all</Button>
            {backToView && <Button type="button" variant="secondary" size="sm" disabled={sameAsView} onClick={backToView}>Back to the view</Button>}
            <Button type="submit" size="sm" aria-busy={counting || undefined}>
              {count && counted?.key === key ? `Search (${counted.n})` : 'Search'}
            </Button>
          </div>
        </div>
      </form>
    </div>
  )
}

function Marked({ text, find }: { text: string; find: string }) {
  const at = find ? text.toLowerCase().indexOf(find) : -1
  if (at < 0) return <>{text}</>
  return <>{text.slice(0, at)}<mark className="bg-yellow-200">{text.slice(at, at + find.length)}</mark>{text.slice(at + find.length)}</>
}
