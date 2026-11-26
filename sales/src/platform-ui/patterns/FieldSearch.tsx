import * as RP from '@radix-ui/react-popover'
import { useRef, useState, type ReactNode } from 'react'
import { Select } from '../primitives/Select'
import { submitOnEnter } from '../primitives/submitOnEnter'
import { Button } from '../primitives/shell'
import { Hint } from '../primitives/Text'
import type { FieldMeta } from '../resource/listMeta'
import type { Filter } from '../resource/querySpec'
import { opLabel, panelOps } from './listView'
import { cleared, draftFrom, filterFrom, type Draft, type DraftOp } from './searchDraft'
import { ValueInput } from './ValueInput'

export interface FieldSearchProps {
  field: FieldMeta
  /** That field's current filters (what the popover starts from). */
  filters: Filter[]
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The field's search as it now is: none (cleared) or one filter; replaces the field's filters. */
  onApply: (filters: Filter[]) => void
  /** "All fields…": the whole search panel, this field in it. */
  onAllFields?: () => void
  suggest?: (field: string, query: string) => Promise<string[]>
  /** Where it opens and where focus goes back on close: a column header's control, a Search chip. */
  children: ReactNode
}

/** One field's search, at its column or its chip (#32 a / c): only that field's operator and values; Search applies it,
 *  Clear empties it (Search then removes it), All fields… opens the panel. Escape closes; focus goes back to where it opened. */
export function FieldSearch({ field, filters, open, onOpenChange, onApply, onAllFields, suggest, children }: FieldSearchProps) {
  const anchor = useRef<HTMLSpanElement>(null)
  const elsewhere = useRef(false)  // closed for the whole panel: focus goes there, not back here
  return (
    <RP.Root open={open} onOpenChange={onOpenChange}>
      <RP.Anchor asChild><span ref={anchor} className="inline-flex items-center">{children}</span></RP.Anchor>
      <RP.Portal>
        <RP.Content align="start" sideOffset={6} aria-label={`Search ${field.label}`} role="dialog"
          onCloseAutoFocus={(e) => {
            e.preventDefault()
            if (!elsewhere.current) anchor.current?.querySelector<HTMLElement>('button')?.focus()
            elsewhere.current = false
          }}
          className="z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2 rounded-lg border border-neutral-200 bg-white p-3 shadow-lg focus:outline-none">
          {open && <FieldSearchForm field={field} filters={filters} suggest={suggest}
            onAllFields={onAllFields && (() => { elsewhere.current = true; onAllFields() })}
            onApply={(next) => { onApply(next); onOpenChange(false) }} />}
        </RP.Content>
      </RP.Portal>
    </RP.Root>
  )
}

function FieldSearchForm({ field, filters, onApply, onAllFields, suggest }: Pick<FieldSearchProps, 'field' | 'filters' | 'onApply' | 'onAllFields' | 'suggest'>) {
  const [d, setD] = useState<Draft>(() => ({ ...draftFrom(field, filters), on: true }))
  const set = (p: Partial<Draft>) => setD((x) => ({ ...x, ...p }))
  const ops = panelOps(field)
  const apply = () => { const f = filterFrom(field, d); onApply(f ? [f] : []) }
  return (
    <form className="flex flex-col gap-2" onKeyDownCapture={submitOnEnter} onSubmit={(e) => { e.preventDefault(); apply() }}>
      <h3 className="text-sm font-semibold text-neutral-800">{field.label}</h3>
      {ops.length > 1 && (
        <Select aria-label={`${field.label}: how to compare`} value={d.op}
          onChange={(op) => set({ op: (op ?? ops[0]) as DraftOp, ...(op === 'within' || d.op === 'within' ? { values: [] } : {}) })}
          options={ops.map((op) => ({ value: op, label: opLabel(op, field.type) }))} />
      )}
      <ValueInput field={field} draft={d} set={set} autoFocus suggest={suggest} />
      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-neutral-100 pt-2">
        <Hint as="span" className="mr-auto">Enter adds · Enter again searches</Hint>
        {onAllFields && <Button type="button" variant="ghost" size="sm" onClick={onAllFields}>All fields…</Button>}
        <Button type="button" variant="secondary" size="sm" onClick={() => setD(cleared(d, true))}>Clear</Button>
        <Button type="submit" size="sm">Search</Button>
      </div>
    </form>
  )
}
