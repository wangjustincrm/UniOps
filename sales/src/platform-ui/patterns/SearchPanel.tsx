import { useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { Checkbox, CheckboxList } from '../primitives/Checkbox'
import { DateInput, DateTimeInput, NumberInput } from '../primitives/FieldInputs'
import { Select } from '../primitives/Select'
import { Button } from '../primitives/shell'
import { TagInput } from '../primitives/TagInput'
import type { FieldMeta } from '../resource/listMeta'
import type { Filter, FilterOp } from '../resource/querySpec'
import { opLabel, panelOps } from './listView'
import { ToolbarPopover } from './ToolbarPopover'

interface Draft {
  on: boolean
  op: FilterOp
  values: string[]
  draft: string
  low: string
  high: string
}

// Values are kept as the server reads them: a date "YYYY-MM-DD" (never
// through new Date), a date-time ISO with its zone (DateTimeInput converts).
const tagType = (f: FieldMeta) => (f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : 'text')

function draftFrom(field: FieldMeta, filters: Filter[]): Draft {
  const ops = panelOps(field)
  const f = filters.find((x) => x.field === field.name)
  const base: Draft = { on: false, op: ops[0], values: [], draft: '', low: '', high: '' }
  if (!f) return base
  const op = f.op === 'range' ? 'between' : f.op === 'eq' && field.type === 'enum' ? 'in' : f.op === 'ne' && field.type === 'enum' ? 'not_in' : f.op
  if (op === 'between') {
    const [low, high] = (f.value ?? [null, null]) as [string | null, string | null]
    return { ...base, on: true, op, low: low ?? '', high: high ?? '' }
  }
  const values = f.value == null ? [] : Array.isArray(f.value) ? (f.value as string[]) : [String(f.value)]
  return { ...base, on: true, op, values }
}

/** The filter a field's draft stands for, or null when it says nothing yet. */
function filterFrom(field: FieldMeta, d: Draft): Filter | null {
  if (!d.on) return null
  if (d.op === 'is_null' || d.op === 'not_null') return { field: field.name, op: d.op, value: null }
  if (d.op === 'between') {
    return d.low || d.high ? { field: field.name, op: 'between', value: [d.low || null, d.high || null] } : null
  }
  const values = [...d.values, ...(d.draft.trim() && !d.values.includes(d.draft.trim()) ? [d.draft.trim()] : [])]
  if (values.length === 0) return null
  if (d.op === 'gt' || d.op === 'gte' || d.op === 'lt' || d.op === 'lte') return { field: field.name, op: d.op, value: values[0] }
  if (d.op === 'in' || d.op === 'not_in') return { field: field.name, op: d.op, value: values }
  return { field: field.name, op: d.op, value: values.length === 1 ? values[0] : values }
}

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
}

/** Search on any field: tick it, choose how to compare, give one or more
 *  values (any of them matches; for "not" operators none may). Different
 *  fields must all match. */
export function SearchPanel({ open, onOpenChange, ...rest }: SearchPanelProps) {
  // The toolbar's Search icon; its badge counts the conditions set here.
  const active = new Set(rest.filters.map((f) => f.field)).size
  return (
    <ToolbarPopover label="Search on any field" icon={<SlidersHorizontal className="h-4 w-4" />} badge={active || undefined}
      title="Search" open={open} onOpenChange={onOpenChange} width="w-[26rem]">
      <p className="text-xs text-neutral-500">Values of one field: any of them. Different fields: all of them.</p>
      {/* Remounted on every open, so the drafts start from the current filters. */}
      {open && <SearchForm {...rest} close={() => onOpenChange(false)} />}
    </ToolbarPopover>
  )
}

function SearchForm({ fields, filters, focusField, onApply, close }: Omit<SearchPanelProps, 'open' | 'onOpenChange'> & { close: () => void }) {
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
    <form className="flex flex-col gap-1" onSubmit={(e) => { e.preventDefault(); apply() }}>
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
                      onChange={(op) => set(f.name, { op: (op ?? ops[0]) as FilterOp })}
                      options={ops.map((op) => ({ value: op, label: opLabel(op, f.type) }))}
                    />
                  )}
                  <ValueInput field={f} draft={d} set={(p) => set(f.name, p)} autoFocus={f.name === focusField} />
                </div>
              )}
            </li>
          )
        })}
      </ul>
      <div className="sticky -bottom-3 -mb-3 flex justify-end gap-2 border-t border-neutral-100 bg-white pb-3 pt-3">
        <Button type="button" variant="secondary" size="sm" onClick={() => setDrafts((all) => Object.fromEntries(
          Object.entries(all).map(([k, v]) => [k, { ...v, on: false, values: [], draft: '', low: '', high: '' }])))}>Clear</Button>
        <Button type="submit" size="sm">Search</Button>
      </div>
    </form>
  )
}

function ValueInput({ field, draft, set, autoFocus }: { field: FieldMeta; draft: Draft; set: (p: Partial<Draft>) => void; autoFocus: boolean }) {
  const label = `${field.label}: value`
  if (draft.op === 'is_null' || draft.op === 'not_null') return null
  if (field.type === 'enum') {
    return <CheckboxList label={label} options={field.enum_values.map((v) => ({ value: v, label: v }))}
      values={draft.values} onChange={(values) => set({ values })} />
  }
  if (field.type === 'bool') {
    return <Select aria-label={label} value={draft.values[0] ?? null} onChange={(v) => set({ values: v ? [v] : [] })}
      options={[{ value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }]} />
  }
  // One value of the field's kind: low / high of between, or gt…lte.
  const one = (aria: string, value: string, onValue: (v: string) => void, focus: boolean) =>
    field.type === 'datetime'
      ? <DateTimeInput aria-label={aria} value={value || null} autoFocus={focus} onChange={(iso) => onValue(iso ?? '')} />
      : field.type === 'date'
        ? <DateInput aria-label={aria} value={value} autoFocus={focus} onChange={(e) => onValue(e.target.value)} />
        : <NumberInput aria-label={aria} value={value} autoFocus={focus} onChange={(e) => onValue(e.target.value)} />
  if (draft.op === 'between') {
    return (
      <div className="flex items-center gap-2">
        {one(`${field.label}: from`, draft.low, (low) => set({ low }), autoFocus)}
        <span className="text-neutral-400">and</span>
        {one(`${field.label}: to`, draft.high, (high) => set({ high }), false)}
      </div>
    )
  }
  if (draft.op === 'gt' || draft.op === 'gte' || draft.op === 'lt' || draft.op === 'lte') {
    return one(label, draft.values[0] ?? draft.draft, (v) => set({ values: [], draft: v }), autoFocus)
  }
  return (
    <TagInput label={label} type={tagType(field)} values={draft.values} onChange={(values) => set({ values })}
      draft={draft.draft} onDraftChange={(v) => set({ draft: v })} autoFocus={autoFocus} />
  )
}
