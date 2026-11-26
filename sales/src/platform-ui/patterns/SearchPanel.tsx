import { useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { Checkbox } from '../primitives/Checkbox'
import { Select } from '../primitives/Select'
import { submitOnEnter } from '../primitives/submitOnEnter'
import { Button } from '../primitives/shell'
import { Hint } from '../primitives/Text'
import type { FieldMeta } from '../resource/listMeta'
import type { Filter } from '../resource/querySpec'
import { cleared, draftFrom, filterFrom, type Draft, type DraftOp } from './searchDraft'
import { ValueInput } from './ValueInput'
import { opLabel, panelOps } from './listView'
import { ToolbarPopover } from './ToolbarPopover'
import { TourMark } from '../help'

export interface SearchPanelProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The filterable fields the user may set (locked ones left out). */
  fields: FieldMeta[]
  /** The current filters of those fields. */
  filters: Filter[]
  /** Opened from a column's menu: that field is ticked and focused. */
  focusField?: string | null
  /** Every filter of these fields, replacing the current ones. */
  onApply: (filters: Filter[]) => void
  /** Values of other records for a field, while typing (fields marked suggestable). */
  suggest?: (field: string, query: string) => Promise<string[]>
}

/** Search on any field: tick it, choose how to compare, give one or more
 *  values (any of them matches; for "not" operators none may). Different
 *  fields must all match. */
export function SearchPanel({ open, onOpenChange, ...rest }: SearchPanelProps) {
  // The toolbar's Search icon; its badge counts the conditions set here.
  const active = new Set(rest.filters.map((f) => f.field)).size
  return (
    <TourMark help="list.searchPanel" slot="list.searchPanel" also={{ slot: 'list.statusFind', text: 'list.statusFind' }} className="inline-flex"><ToolbarPopover label="Search on any field" icon={<SlidersHorizontal className="h-4 w-4" />} badge={active || undefined}
      title="Search" open={open} onOpenChange={onOpenChange} width="w-[26rem]">
      <p className="text-xs text-neutral-500">Values of one field: any of them. Different fields: all of them.</p>
      {/* Remounted on every open, so the drafts start from the current filters. */}
      {open && <SearchForm {...rest} close={() => onOpenChange(false)} />}
    </ToolbarPopover></TourMark>
  )
}

function SearchForm({ fields, filters, focusField, onApply, suggest, close }: Omit<SearchPanelProps, 'open' | 'onOpenChange'> & { close: () => void }) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>(() => Object.fromEntries(fields.map((f) => {
    const d = draftFrom(f, filters)
    return [f.name, f.name === focusField ? { ...d, on: true } : d]
  })))
  const set = (name: string, patch: Partial<Draft>) => setDrafts((all) => ({ ...all, [name]: { ...all[name], ...patch } }))
  const apply = () => {
    onApply(fields.map((f) => filterFrom(f, drafts[f.name])).filter((x): x is Filter => x !== null))
    close()
  }
  return (
    <form className="flex flex-col gap-1" onKeyDownCapture={submitOnEnter} onSubmit={(e) => { e.preventDefault(); apply() }}>
      <ul className="flex flex-col divide-y divide-neutral-100">
        {fields.map((f) => {
          const d = drafts[f.name]
          const ops = panelOps(f)
          return (
            <li key={f.name} className="py-2">
              <Checkbox label={f.label} checked={d.on} onCheckedChange={(on) => set(f.name, { on })}
                className="text-sm font-medium text-neutral-800" />
              {d.on && (
                <div className="mt-2 flex flex-col gap-2 pl-6">
                  {ops.length > 1 && (
                    <Select
                      aria-label={`${f.label}: how to compare`}
                      value={d.op}
                      onChange={(op) => set(f.name, { op: (op ?? ops[0]) as DraftOp, ...(op === 'within' || d.op === 'within' ? { values: [] } : {}) })}
                      options={ops.map((op) => ({ value: op, label: opLabel(op, f.type) }))}
                    />
                  )}
                  <ValueInput field={f} draft={d} set={(p) => set(f.name, p)} autoFocus={f.name === focusField} suggest={suggest} />
                </div>
              )}
            </li>
          )
        })}
      </ul>
      <div className="sticky -bottom-3 -mb-3 flex justify-end gap-2 border-t border-neutral-100 bg-white pb-3 pt-3">
        <Hint as="span" className="mr-auto self-center">Enter adds · Enter again searches</Hint>
        <Button type="button" variant="secondary" size="sm" onClick={() => setDrafts((all) => Object.fromEntries(
          Object.entries(all).map(([k, v]) => [k, cleared(v)])))}>Clear</Button>
        <Button type="submit" size="sm">Search</Button>
      </div>
    </form>
  )
}
