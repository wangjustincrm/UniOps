import { describe, expect, it } from 'vitest'
import { clearSubmitFailure, errorMessage, peekSubmitFailure, rememberSubmitFailure } from './submitFailure'

describe('submit failure notice (ISS-0090, ISS-0166)', () => {
  it('carries the refusal to the draft page once', () => {
    rememberSubmitFailure('doc-1', 'Saved as a draft but not submitted: no stage')
    expect(peekSubmitFailure('doc-1')).toBe('Saved as a draft but not submitted: no stage')
    expect(peekSubmitFailure('doc-2')).toBeNull()
    clearSubmitFailure('doc-1')
    expect(peekSubmitFailure('doc-1')).toBeNull()
  })

  it('reads the server message off an error, else the fallback', () => {
    expect(errorMessage(new Error('Cannot route approval'), 'x')).toBe('Cannot route approval')
    expect(errorMessage('boom', 'fallback')).toBe('fallback')
    expect(peekSubmitFailure(undefined)).toBeNull()
  })
})
