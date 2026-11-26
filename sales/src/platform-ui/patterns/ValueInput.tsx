import { DateInput, DateTimeInput, NumberInput } from '../primitives/FieldInputs'
import { MultiSelect } from '../primitives/MultiSelect'
import { Select } from '../primitives/Select'
import { TagInput } from '../primitives/TagInput'
import type { FieldMeta } from '../resource/listMeta'
import { RELATIVE_DATES } from '../resource/relativeDates'
import { tagType, type Draft } from './searchDraft'

/** A field's values as its type takes them (the panel and a column's popover): several as tags (Enter adds one), a
 *  number or a date, a range, a period (relative dates), a choice. */
export function ValueInput({ field, draft, set, autoFocus, suggest }: {
  field: FieldMeta; draft: Draft; set: (p: Partial<Draft>) => void; autoFocus: boolean
  suggest?: (field: string, query: string) => Promise<string[]>
}) {
  const label = `${field.label}: value`
  if (draft.op === 'is_null' || draft.op === 'not_null') return null
  if (draft.op === 'within') {
    return <Select aria-label={`${field.label}: period`} value={draft.values[0] ?? null} onChange={(v) => set({ values: v ? [v] : [] })}
      options={RELATIVE_DATES.map((r) => ({ value: r.value, label: r.label }))} />
  }
  if (field.type === 'enum') {
    return <MultiSelect label={label} options={field.enum_values.map((v) => ({ value: v, label: v }))}
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
      <div className="flex flex-wrap items-center gap-2">
        {one(`${field.label}: from`, draft.low, (low) => set({ low }), autoFocus)}
        <span className="text-neutral-500">and</span>
        {one(`${field.label}: to`, draft.high, (high) => set({ high }), false)}
      </div>
    )
  }
  if (draft.op === 'gt' || draft.op === 'gte' || draft.op === 'lt' || draft.op === 'lte') {
    return one(label, draft.values[0] ?? draft.draft, (v) => set({ values: [], draft: v }), autoFocus)
  }
  if (field.suggestable && suggest) {
    return <MultiSelect label={label} options={[]} freeText values={draft.values} onChange={(values) => set({ values })}
      onDraftChange={(v) => set({ draft: v })} placeholder="Type or choose…"
      loadOptions={(q) => suggest(field.name, q).then((found) => found.map((v) => ({ value: v, label: v })))} />
  }
  return (
    <TagInput label={label} type={tagType(field)} values={draft.values} onChange={(values) => set({ values })}
      draft={draft.draft} onDraftChange={(v) => set({ draft: v })} autoFocus={autoFocus} addOnEnter={field.type === 'text'} />
  )
}
