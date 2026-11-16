import { describe, expect, it, vi } from 'vitest'

vi.mock('@/stores/auth.store', () => ({
  useAuthStore: { getState: () => ({ token: null }) },
}))

import { readLineItems } from './invoice-parser'

// ISS-0232: the unit the AI reads off the invoice pre-fills the Upload form.
describe('readLineItems', () => {
  it('carries the printed unit into the form row', () => {
    const rows = readLineItems([{ description: 'Gloves', quantity: 2, unit: 'box', unit_price: 5, amount: 10 }])
    expect(rows).toEqual([{ description: 'Gloves', quantity: 2, unit: 'box', unit_price: 5, line_total: 10 }])
  })

  it('leaves the unit empty when none was read (or the server is older)', () => {
    const rows = readLineItems([
      { description: 'A', quantity: 1, unit: null, unit_price: 1, amount: 1 },
      { description: 'B', quantity: 1, unit_price: 1, amount: 1 },
      { description: 'C', quantity: 1, unit: '  ', unit_price: 1, amount: 1 },
    ])
    expect(rows!.map((r) => r.unit)).toEqual([null, null, null])
  })

  it('returns null when there are no lines', () => {
    expect(readLineItems([])).toBeNull()
    expect(readLineItems(undefined)).toBeNull()
  })
})
