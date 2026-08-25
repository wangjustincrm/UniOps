import { describe, expect, it } from 'vitest'
import {
  isImportedEditablePo, isImportedTaxLocked, isNcSourced,
  manualLinesTotal, subtotalWith,
} from './importedPoEdit'

// These two predicates exist because the same condition used to be written out
// twice — on the Edit Detail button and inside the form page. Widening one and
// not the other shipped a button that led to a refusal, and tsc had nothing to
// say about it. The value here is not the logic (it is one comparison); it is
// that there is now exactly one copy of it.

describe('isImportedEditablePo', () => {
  it.each([
    'issued', 'nc_pending', 'nc_milk', 'closed', 'draft', 'cancelled',
    'partially_received', 'fully_received',
  ])('accepts an NC PO in status %s', (status) => {
    expect(isImportedEditablePo({ source: 'nc', status } as never)).toBe(true)
  })

  it('rejects a PO that did not come from NC', () => {
    expect(isImportedEditablePo({ source: null })).toBe(false)
    expect(isImportedEditablePo({ source: 'pms' })).toBe(false)
    expect(isImportedEditablePo({})).toBe(false)
  })

  it('rejects nothing at all rather than throwing while the PO loads', () => {
    expect(isImportedEditablePo(undefined)).toBe(false)
    expect(isImportedEditablePo(null)).toBe(false)
  })
})

describe('isImportedTaxLocked', () => {
  it('locks the rate once an invoice points at the PO', () => {
    expect(isImportedTaxLocked({ source: 'nc', has_invoice: true })).toBe(true)
  })

  it('leaves it open while no invoice does', () => {
    expect(isImportedTaxLocked({ source: 'nc', has_invoice: false })).toBe(false)
  })

  it('treats a missing flag as unlocked, not as an error', () => {
    // An older API build, or any response that is not the detail endpoint,
    // simply omits the key.
    expect(isImportedTaxLocked({ source: 'nc' })).toBe(false)
    expect(isImportedTaxLocked(undefined)).toBe(false)
  })
})

describe('isNcSourced', () => {
  const nc = { source: 'nc' }

  it('takes the flag at its word when the server sent one', () => {
    expect(isNcSourced({ nc_sourced: true }, nc)).toBe(true)
    expect(isNcSourced({ nc_sourced: false }, nc)).toBe(false)
  })

  it('treats a missing flag on an NC PO as the ERP owning the line', () => {
    // Safer than the alternative: offering to edit an NC line produces a
    // refusal at the endpoint, which is at least visible.
    expect(isNcSourced({}, nc)).toBe(true)
  })

  it('treats a missing flag on a native PO as not-NC', () => {
    expect(isNcSourced({}, { source: null })).toBe(false)
  })
})

describe('manualLinesTotal', () => {
  const ncLine = { ncSourced: true, qty: 10, unitPrice: 10 }
  const tooling = { ncSourced: false, qty: 1, unitPrice: 5000 }

  it('counts only the buyer-added lines', () => {
    expect(manualLinesTotal([ncLine, tooling])).toBe(5000)
  })

  it('is zero when nothing was added', () => {
    expect(manualLinesTotal([ncLine])).toBe(0)
    expect(manualLinesTotal([])).toBe(0)
  })

  it('allows a negative line — a one-off credit is a real line', () => {
    expect(manualLinesTotal([{ ncSourced: false, qty: 1, unitPrice: -250 }])).toBe(-250)
  })

  it('rounds each line before summing, as the server does', () => {
    // 3 x 0.335 = 1.005 -> 1.01 per line, not 1.005 carried into the sum.
    const line = { ncSourced: false, qty: 3, unitPrice: 0.335 }
    expect(manualLinesTotal([line, line])).toBe(2.02)
  })
})

describe('subtotalWith', () => {
  it('rebuilds the subtotal around NC’s own figure', () => {
    // Stored 5100 = NC's 100 + a 5000 tooling line already saved.
    const edited = [{ ncSourced: true, qty: 10, unitPrice: 10 },
                    { ncSourced: false, qty: 1, unitPrice: 4000 }]
    expect(subtotalWith(5100, 5000, edited)).toBe(4100)
  })

  it('drops back to NC’s figure when the added line is removed', () => {
    expect(subtotalWith(5100, 5000, [{ ncSourced: true, qty: 10, unitPrice: 10 }]))
      .toBe(100)
  })

  it('leaves a PO that never had a manual line alone', () => {
    expect(subtotalWith(100, 0, [{ ncSourced: true, qty: 10, unitPrice: 10 }]))
      .toBe(100)
  })
})
