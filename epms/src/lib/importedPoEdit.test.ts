import { describe, expect, it } from 'vitest'
import { isImportedEditablePo, isImportedTaxLocked } from './importedPoEdit'

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
