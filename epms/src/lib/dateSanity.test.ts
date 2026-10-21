import { describe, expect, it } from 'vitest'
import { invoiceDateWarnings, receiptDateWarnings } from './dateSanity'
import { todayISODate } from './utils'

const codes = (w: { code: string }[]) => w.map((x) => x.code)

describe('invoiceDateWarnings (ISS-0075)', () => {
  const today = '2026-09-24'

  it('a plausible invoice raises nothing', () => {
    expect(invoiceDateWarnings('2026-09-20', '2026-10-20', today)).toEqual([])
    expect(invoiceDateWarnings(today, '', today)).toEqual([])
  })

  it('INV-2026-0535: 07/12/2026 read as 7 Dec, uploaded 24/09 — future, swapped reading offered', () => {
    const w = invoiceDateWarnings('2026-12-07', '2026-08-11', today)
    expect(codes(w)).toEqual(['future', 'due_before_invoice'])
    expect(w[0].message).toContain('12/07/2026')
  })

  it('a future date that cannot be swapped still warns', () => {
    const w = invoiceDateWarnings('2026-11-25', '', today)
    expect(codes(w)).toEqual(['future'])
    expect(w[0].message).not.toContain('swapped')
  })

  it('INV-2026-0425: 26/09/2004 — more than a year ago', () => {
    expect(codes(invoiceDateWarnings('2004-09-26', '2004-10-26', today))).toEqual(['over_a_year_ago'])
    // exactly a year back is not "more than a year"
    expect(invoiceDateWarnings('2025-09-24', '', today)).toEqual([])
    expect(codes(invoiceDateWarnings('2025-09-23', '', today))).toEqual(['over_a_year_ago'])
  })

  it('due date before invoice date', () => {
    expect(codes(invoiceDateWarnings('2026-09-10', '2026-09-01', today))).toEqual(['due_before_invoice'])
  })

  it('ignores an empty or invalid date rather than guessing', () => {
    expect(invoiceDateWarnings('', '', today)).toEqual([])
    expect(invoiceDateWarnings('2026-02-31', '', today)).toEqual([])
  })
})

describe('receiptDateWarnings (ISS-0075)', () => {
  const today = '2026-10-20'

  it('AGR-202608-0004 counter slips dated 2004 / 2030', () => {
    expect(codes(receiptDateWarnings('2004-08-26', today, '2026-08-01'))).toEqual(['before_valid_from'])
    expect(codes(receiptDateWarnings('2030-08-26', today, '2026-08-01'))).toEqual(['future'])
  })

  it('inside the agreement and not in the future: nothing', () => {
    expect(receiptDateWarnings('2026-08-26', today, '2026-08-01')).toEqual([])
    expect(receiptDateWarnings('2026-08-01', today, '2026-08-01')).toEqual([])
    expect(receiptDateWarnings('2026-08-26', today, null)).toEqual([])
  })
})

describe('todayISODate', () => {
  it('is the local calendar date, not the UTC one', () => {
    // 21:30 local on 20 Oct — in any zone west of UTC this is already the
    // 21st in UTC; the local date must still be the 20th.
    expect(todayISODate(new Date(2026, 9, 20, 21, 30))).toBe('2026-10-20')
    expect(todayISODate(new Date(2026, 0, 5, 0, 1))).toBe('2026-01-05')
  })
})
