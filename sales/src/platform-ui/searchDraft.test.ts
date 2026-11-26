import { describe, expect, it } from 'vitest'
import type { FieldMeta } from './resource/listMeta'
import type { Filter, FilterOp } from './resource/querySpec'
import { RELATIVE_DATES } from './resource/relativeDates'
import { cleared, draftFrom, filterFrom } from './patterns/searchDraft'
import { filterText, opLabel, panelOps } from './patterns/listView'

const ORDERED: FilterOp[] = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'not_in', 'is_null', 'not_null']
const field = (name: string, type: FieldMeta['type'], operators: FilterOp[] = ORDERED, enum_values: string[] = []): FieldMeta =>
  ({ name, label: name[0].toUpperCase() + name.slice(1), type, enum_values, filterable: true, sortable: true, groupable: false, operators })
const F = {
  text: field('code', 'text', ['contains', 'not_contains', 'starts_with', 'ends_with', 'eq', 'ne', 'in', 'not_in', 'is_null', 'not_null']),
  number: field('price', 'number'), date: field('ordered', 'date'), datetime: field('received', 'datetime'),
  enum: field('status', 'enum', ['eq', 'ne', 'in', 'not_in', 'is_null', 'not_null'], ['A', 'B']), bool: field('active', 'bool', ['eq', 'ne']),
}

// What a field's draft stands for, back and forth (#32 stage 1: one Draft logic for the panel and a column's popover).
const CASES: [FieldMeta, Filter][] = [
  [F.text, { field: 'code', op: 'contains', value: ['a', 'b'] }], [F.text, { field: 'code', op: 'eq', value: 'x' }],
  [F.text, { field: 'code', op: 'is_null', value: null }], [F.number, { field: 'price', op: 'gte', value: '5' }],
  [F.number, { field: 'price', op: 'between', value: ['1', null] }], [F.date, { field: 'ordered', op: 'between', value: ['2026-11-01', '2026-11-26'] }],
  [F.date, { field: 'ordered', op: 'eq', value: '2026-11-26' }], [F.datetime, { field: 'received', op: 'gte', value: '2026-11-26T05:00:00.000Z' }],
  [F.enum, { field: 'status', op: 'in', value: ['A', 'B'] }], [F.enum, { field: 'status', op: 'not_in', value: ['A'] }],
  [F.bool, { field: 'active', op: 'eq', value: 'true' }],
  ...RELATIVE_DATES.map((r) => [F.date, { field: 'ordered', op: 'eq', value: r.value }] as [FieldMeta, Filter]),
  [F.datetime, { field: 'received', op: 'eq', value: 'last_7_days' }],
]

describe('a search draft', () => {
  it.each(CASES.map(([f, x]) => [f.type, JSON.stringify(x.value), f, x] as const))('%s %s: the filter comes back as it was', (_t, _v, f, x) => {
    expect(filterFrom(f, draftFrom(f, [x]))).toEqual(x)
  })

  it('a period is "within" in the draft and eq with its token on the wire; a chip says its words', () => {
    const d = draftFrom(F.datetime, [{ field: 'received', op: 'eq', value: 'last_7_days' }])
    expect([d.op, d.values]).toEqual(['within', ['last_7_days']])
    expect(filterText({ field: 'received', op: 'eq', value: 'last_7_days' }, F.datetime)).toBe('Received: last 7 days')
    expect(filterText({ field: 'ordered', op: 'eq', value: 'this_month' }, F.date)).toBe('Ordered: this month')
    expect(filterText({ field: 'code', op: 'eq', value: 'today' }, F.text)).toBe('Code equals "today"')  // a word in a text field
    expect(panelOps(F.date)[0]).toBe('within')
    expect(panelOps(F.datetime)[0]).toBe('within')
    expect(panelOps(F.number)).not.toContain('within')
    expect(panelOps(field('d', 'date', ['gte', 'lte']))).not.toContain('within')  // only where the server allows eq
    expect(opLabel('within', 'date')).toBe('is within')
  })

  it('what is typed and not added still counts; nothing set is no filter; Clear empties it', () => {
    const d = { ...draftFrom(F.text, []), on: true, op: 'contains' as const, values: ['a'], draft: ' b ' }
    expect(filterFrom(F.text, d)).toEqual({ field: 'code', op: 'contains', value: ['a', 'b'] })
    expect(filterFrom(F.text, { ...d, values: [], draft: '' })).toBeNull()
    expect(filterFrom(F.date, { ...draftFrom(F.date, []), on: true, op: 'within', values: [] })).toBeNull()
    expect(filterFrom(F.text, cleared(d))).toBeNull()
    expect(cleared(d, true)).toMatchObject({ on: true, values: [], draft: '' })
  })
})
