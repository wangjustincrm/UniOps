import type { FieldMeta, FieldKind } from '../resource/listMeta'
import type { Filter, FilterOp } from '../resource/querySpec'
import { relativeDate } from '../resource/relativeDates'
import type { DraftOp } from './searchDraft'

// What the search panel offers per field type, how each operator reads, and
// how an active filter reads as a chip. The server lists what a field allows
// (FieldMeta.operators); the panel shows the intersection.

export const PANEL_OPS: Record<FieldKind, FilterOp[]> = {
  text: ['contains', 'not_contains', 'starts_with', 'ends_with', 'eq', 'ne', 'is_null', 'not_null'],
  number: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'is_null'],
  date: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'is_null'],
  datetime: ['gte', 'lte', 'gt', 'lt', 'between', 'is_null'],
  enum: ['in', 'not_in'],
  bool: ['eq', 'ne'],
}

const ORDERED: Partial<Record<FilterOp, string>> = { eq: '=', ne: '≠', gt: '>', gte: '≥', lt: '<', lte: '≤' }
const TEXT: Partial<Record<FilterOp, string>> = {
  contains: 'contains', not_contains: 'does not contain', starts_with: 'starts with', ends_with: 'ends with',
  eq: 'equals', ne: 'not equals', in: 'is any of', not_in: 'is none of',
}

export function opLabel(op: DraftOp, type: FieldKind): string {
  if (op === 'within') return 'is within'
  if (op === 'is_null') return 'is empty'
  if (op === 'not_null') return 'is not empty'
  if (op === 'between' || op === 'range') return 'between'
  if (type === 'enum' || type === 'bool') return op === 'in' || op === 'eq' ? 'is' : 'is not'
  if (type === 'number' || type === 'date' || type === 'datetime') return ORDERED[op] ?? TEXT[op] ?? op
  return TEXT[op] ?? ORDERED[op] ?? op
}

export function panelOps(field: FieldMeta): DraftOp[] {
  const allowed = new Set(field.operators)
  const ops: DraftOp[] = PANEL_OPS[field.type].filter((op) => allowed.has(op))
  // A date field searched by a period first (#32): "within" Today, Last 7 days… (eq with the period's token on the wire).
  return (field.type === 'date' || field.type === 'datetime') && allowed.has('eq') ? ['within', ...ops] : ops
}

const quote = (v: string) => `"${v}"`

/** `Item name contains "l" or "m"` — an active filter as a chip reads it. */
export function filterText(f: Filter, field: FieldMeta | undefined): string {
  const label = field?.label ?? f.field
  const op = opLabel(f.op, field?.type ?? 'text')
  if (f.op === 'is_null' || f.op === 'not_null') return `${label} ${op}`
  const period = f.op === 'eq' && (field?.type === 'date' || field?.type === 'datetime') ? relativeDate(f.value) : undefined
  if (period) return `${label}: ${period.label.toLowerCase()}`  // "Order date: last 7 days", never the token
  if (f.op === 'between' || f.op === 'range') {
    const [low, high] = (f.value ?? [null, null]) as [string | null, string | null]
    if (low && high) return `${label} between ${low} and ${high}`
    return low ? `${label} ≥ ${low}` : `${label} ≤ ${high ?? ''}`
  }
  const values = Array.isArray(f.value) ? (f.value as string[]) : [String(f.value ?? '')]
  const negative = f.op === 'ne' || f.op === 'not_contains' || f.op === 'not_in'
  return `${label} ${op} ${values.map(quote).join(negative ? ' nor ' : ' or ')}`
}
