import { describe, expect, it } from 'vitest'
import { normalizeEmailList } from './emailList'

describe('normalizeEmailList', () => {
  it('accepts a single address unchanged', () => {
    expect(normalizeEmailList('kyle@sangersinc.com')).toEqual({
      valid: true,
      normalized: 'kyle@sangersinc.com',
    })
  })

  it('accepts a comma-separated list and normalizes the spacing', () => {
    expect(normalizeEmailList('kyle@sangersinc.com,rob@sangersinc.com')).toEqual({
      valid: true,
      normalized: 'kyle@sangersinc.com, rob@sangersinc.com',
    })
  })

  it('rewrites semicolon separators to commas', () => {
    // A semicolon is not an RFC 5322 separator. Left as-is, the whole To
    // header collapses to a single EMPTY recipient at send time — which is
    // exactly how Sangers Inc's remittance advice died with
    // `501 5.1.3 Bad recipient address syntax` on 2026-08-17.
    expect(normalizeEmailList('kyle@sangersinc.com; rob@sangersinc.com')).toEqual({
      valid: true,
      normalized: 'kyle@sangersinc.com, rob@sangersinc.com',
    })
  })

  it('drops a trailing separator instead of keeping an empty address', () => {
    expect(normalizeEmailList('a@x.com, b@x.com,')).toEqual({
      valid: true,
      normalized: 'a@x.com, b@x.com',
    })
  })

  it('rejects the list when any one address is malformed', () => {
    expect(normalizeEmailList('a@x.com, not-an-email')).toEqual({
      valid: false,
      normalized: 'a@x.com, not-an-email',
    })
  })

  it('treats a blank value as empty rather than invalid', () => {
    expect(normalizeEmailList('   ')).toEqual({ valid: true, normalized: '' })
  })
})
