import { describe, expect, it } from 'vitest'
import type { ColumnDef } from '@/platform-ui'
import { itemColumns, lotColumns } from './inventoryColumns'

const key = (c: ColumnDef<never, unknown>) => (c as { accessorKey?: string; id?: string }).accessorKey ?? (c as { id?: string }).id
const hidden = (c: ColumnDef<never, unknown>) => !!(c.meta as { hidden?: boolean } | undefined)?.hidden
const shown = (cols: ColumnDef<never, unknown>[]) => cols.filter((c) => !hidden(c)).map(key)

// User 2026-11-16: a row reads received − shipped = on hand; on hand − in process − on hold = available,
// with On hand shown by default, left of In process, On hold and Available, for an item and for a lot.
describe('inventory columns', () => {
  it('an item shows on hand before in process, on hold and available', () => {
    const cols = shown(itemColumns as ColumnDef<never, unknown>[])
    const order = ['received_ea', 'shipped_ea', 'on_hand_ea', 'allocated_ea', 'hold_ea', 'available_ea']
    expect(order.map((k) => cols.indexOf(k))).toEqual([...order.map((k) => cols.indexOf(k))].sort((a, b) => a - b))
    expect(order.every((k) => cols.includes(k))).toBe(true)
  })

  it('a lot shows on hand before in process, on hold and available', () => {
    const cols = shown(lotColumns as ColumnDef<never, unknown>[])
    const order = ['received_ea', 'shipped_ea', 'on_hand_ea', 'in_process_ea', 'hold_ea', 'available_ea']
    expect(order.every((k) => cols.includes(k))).toBe(true)
    expect(order.map((k) => cols.indexOf(k))).toEqual([...order.map((k) => cols.indexOf(k))].sort((a, b) => a - b))
  })
})
