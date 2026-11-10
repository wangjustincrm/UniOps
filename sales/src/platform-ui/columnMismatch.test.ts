import { describe, expect, it } from 'vitest'
import { columnMismatch, type ListMeta } from './resource/listMeta'

const meta = (over: Partial<ListMeta>): ListMeta => ({ version: 1, searchable: false, default_sort: [], fields: [
  { name: 'total', label: 'Total', type: 'number', enum_values: [], filterable: true, sortable: true, groupable: false, operators: [] },
], ...over })

describe('columnMismatch', () => {
  it('agrees when every named id is a field or a page column and the page has the client columns', () => {
    expect(columnMismatch(meta({ client_columns: ['detail'], views: [{ key: 'v', name: 'V', state: { version: 1, columns: ['total', 'detail'] } }] }),
      ['detail', 'code'])).toEqual([])
  })
  it('names a typo in a view and a client column the page lacks', () => {
    expect(columnMismatch(meta({ client_columns: ['detail'], views: [{ key: 'v', name: 'V', state: { version: 1, hidden: ['payment'] } }] }),
      ['code'])).toEqual(['a system view names "payment", which the page does not have', 'the server expects the page column "detail"'])
  })
})
