import { describe, expect, it } from 'vitest'
import { serviceAcceptanceNotes, serviceHasIssues } from './grNotes'

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
