/**
 * The three type rules that live only in the frontend.
 *
 * The backend's doc_type_guide derives what it can — the submit gates, the
 * receipt flow — so its answer to "what differs between the PR types" cannot
 * drift from what the server enforces. These three cannot be derived there:
 * which types show a Material ID column, which one picks from the Parts
 * Catalog, and which one is not offered at all. They are described in prose in
 * epms-api/app/knowledge/pr_types.yaml, and pinned here so the description and
 * the form cannot part company quietly.
 *
 * Each assertion is paired: what the rule includes AND what it excludes. A test
 * that only checks type 3 shows the picker would still pass if every type did.
 */
import { describe, it, expect } from 'vitest'

import { showMaterialId, isSparePartsType } from './PrLineItems'
import { TYPES } from './ProcurementTypeSelector'
import type { ProcurementType } from '@/types'

const ALL: ProcurementType[] = [1, 2, 3, 4, 5, 6]

describe('procurement type form rules', () => {
  it('shows the Material ID column for types 1 and 3 only', () => {
    expect(ALL.filter(showMaterialId)).toEqual([1, 3])
  })

  it('uses the Parts Catalog picker for type 3 only', () => {
    expect(ALL.filter(isSparePartsType)).toEqual([3])
  })

  it('offers every type except type 1', () => {
    // pr_types.yaml tells a requester type 1 is "not yours to raise". If that
    // ever stops being true, this is where it surfaces.
    expect(TYPES.filter((t) => t.disabled).map((t) => t.type)).toEqual([1])
    expect(TYPES.map((t) => t.type)).toEqual(ALL)
  })

  it('marks type 1 as the one carrying no budget check', () => {
    expect(TYPES.filter((t) => t.noBudget).map((t) => t.type)).toEqual([1])
  })
})
