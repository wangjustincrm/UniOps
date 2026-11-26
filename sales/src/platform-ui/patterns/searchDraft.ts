import type { FieldMeta } from '../resource/listMeta'
import type { Filter, FilterOp } from '../resource/querySpec'
import { relativeDate } from '../resource/relativeDates'
import { panelOps } from './listView'

/** One field's search while it is edited (the panel and a column's popover share it): applied with Search, never on each change. */
export interface Draft {
  on: boolean
  op: DraftOp
  values: string[]
  draft: string
  low: string
  high: string
}

/** "within" a period (relative dates) is eq with a period's token on the wire. */
export type DraftOp = FilterOp | 'within'

// Values are kept as the server reads them: a date "YYYY-MM-DD" (never
// through new Date), a date-time ISO with its zone (DateTimeInput converts).
export const tagType = (f: FieldMeta) => (f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : 'text')

export const EMPTY: Omit<Draft, 'op'> = { on: false, values: [], draft: '', low: '', high: '' }

export function draftFrom(field: FieldMeta, filters: Filter[]): Draft {
  const ops = panelOps(field)
  const f = filters.find((x) => x.field === field.name)
  const base: Draft = { ...EMPTY, op: ops[0] }
  if (!f) return base
  if (f.op === 'eq' && relativeDate(f.value) && ops.includes('within')) return { ...base, on: true, op: 'within', values: [String(f.value)] }
  const op = f.op === 'range' ? 'between' : f.op === 'eq' && field.type === 'enum' ? 'in' : f.op === 'ne' && field.type === 'enum' ? 'not_in' : f.op
  if (op === 'between') {
    const [low, high] = (f.value ?? [null, null]) as [string | null, string | null]
    return { ...base, on: true, op, low: low ?? '', high: high ?? '' }
  }
  const values = f.value == null ? [] : Array.isArray(f.value) ? (f.value as string[]) : [String(f.value)]
  return { ...base, on: true, op, values }
}

/** The filter a field's draft stands for, or null when it says nothing yet (what is typed and not added counts). */
export function filterFrom(field: FieldMeta, d: Draft): Filter | null {
  if (!d.on) return null
  if (d.op === 'is_null' || d.op === 'not_null') return { field: field.name, op: d.op, value: null }
  if (d.op === 'within') return d.values[0] ? { field: field.name, op: 'eq', value: d.values[0] } : null
  if (d.op === 'between') {
    return d.low || d.high ? { field: field.name, op: 'between', value: [d.low || null, d.high || null] } : null
  }
  const values = [...d.values, ...(d.draft.trim() && !d.values.includes(d.draft.trim()) ? [d.draft.trim()] : [])]
  if (values.length === 0) return null
  if (d.op === 'gt' || d.op === 'gte' || d.op === 'lt' || d.op === 'lte') return { field: field.name, op: d.op, value: values[0] }
  if (d.op === 'in' || d.op === 'not_in') return { field: field.name, op: d.op, value: values }
  return { field: field.name, op: d.op, value: values.length === 1 ? values[0] : values }
}

/** The draft emptied, its field still ticked or not as asked. */
export const cleared = (d: Draft, on = false): Draft => ({ ...d, ...EMPTY, on })
