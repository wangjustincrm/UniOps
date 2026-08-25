import { describe, expect, it } from 'vitest'
import { formatAmount, formatUnitPrice } from './utils'

// The ERP quotes to five decimals. Stored in a two-decimal column, 0.944 became
// 0.94 and the line stopped multiplying out — 319 of 4,944 mirrored NC lines,
// 43,256 of error, worst line 2,000 (500,000 x 0.944 read as 500,000 x 0.94).
// Widening the column fixes the storage; this fixes what the reader sees.

describe('formatUnitPrice', () => {
  it('shows the decimals the ERP actually quoted', () => {
    expect(formatUnitPrice(0.944, 'CAD')).toBe('CA$0.944')
    expect(formatUnitPrice(0.2129, 'CAD')).toBe('CA$0.2129')
    expect(formatUnitPrice(0.0575, 'CAD')).toBe('CA$0.0575')
  })

  it('keeps two decimals as the floor, so a price still reads as money', () => {
    expect(formatUnitPrice(10, 'CAD')).toBe('CA$10.00')
    expect(formatUnitPrice(1234.5, 'CAD')).toBe('CA$1,234.50')
  })

  it('trims padding past the second place but nothing meaningful', () => {
    expect(formatUnitPrice(0.944, 'CAD')).not.toContain('0.94400')
  })

  it('stops at five decimals — the column holds no more', () => {
    expect(formatUnitPrice(0.123456, 'CAD')).toBe('CA$0.12346')
  })

  it('carries the same currency marker formatAmount uses', () => {
    // Whatever the symbol is for a code, the two helpers must agree — they sit
    // in adjacent columns of the same table.
    for (const code of ['CAD', 'USD', 'EUR', 'RMB', 'XYZ']) {
      const marker = formatAmount(0, code).replace(/[\d.,\s ]/g, '')
      expect(formatUnitPrice(1, code)).toContain(marker)
    }
  })

  it('handles a negative price — one-off credits are real lines', () => {
    expect(formatUnitPrice(-250, 'CAD')).toContain('250.00')
  })
})
