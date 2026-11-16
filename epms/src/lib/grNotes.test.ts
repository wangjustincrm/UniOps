import { describe, expect, it } from 'vitest'
import { collectionNotesWithLines, serviceAcceptanceNotes, serviceHasIssues } from './grNotes'

describe('service Has Issues on GR Detail (ISS-0179)', () => {
  it('is read back from the acceptance notes Confirm Service Completion wrote', () => {
    const notes = serviceAcceptanceNotes('Cleaning done today ', ' cleaner missed the north corner')
    expect(notes).toBe('Cleaning done today — Issues noted: cleaner missed the north corner')
    expect(serviceHasIssues({ gr_type: 'service', collection_notes: notes })).toBe(true)
  })

  it('is not flagged for a satisfactory service or a physical GR', () => {
    const ok = serviceAcceptanceNotes('Cleaning done today', null)
    expect(ok).toBe('Cleaning done today')
    expect(serviceHasIssues({ gr_type: 'service', collection_notes: ok })).toBe(false)
    expect(serviceHasIssues({ gr_type: 'service', collection_notes: null })).toBe(false)
    expect(serviceHasIssues({ gr_type: 'physical', collection_notes: 'x — Issues noted: y' })).toBe(false)
  })
})

describe('Collect Goods line conditions go into the collection notes (ISS-0185, GR-C-002)', () => {
  const good = { description: 'pump', condition: 'good' as const, notes: '', recordedCondition: 'good' as const }

  it('adds each line marked other than Good after the General Notes', () => {
    expect(collectionNotesWithLines('Picked up at dock 3', [
      { description: 'test tape', condition: 'discrepancy', notes: ' 包装破损（测试） ', recordedCondition: 'good' },
      good,
      { description: 'glue', condition: 'damaged', notes: 'crushed', recordedCondition: 'good' },
    ])).toBe('Picked up at dock 3 · Line conditions at collection: test tape: Discrepancy — 包装破损（测试）; glue: Damaged — crushed')
  })

  it('keeps General Notes alone when every line is Good, and sends nothing when both are empty', () => {
    expect(collectionNotesWithLines(' all fine ', [good])).toBe('all fine')
    expect(collectionNotesWithLines('  ', [good])).toBeUndefined()
  })

  it('notes a line marked Good that the GR recorded as damaged', () => {
    expect(collectionNotesWithLines('', [
      { description: 'pump', condition: 'good', notes: 'old note', recordedCondition: 'damaged' },
    ])).toBe('Line conditions at collection: pump: Good')
  })
})
