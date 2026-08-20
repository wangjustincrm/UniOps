import { describe, expect, it } from 'vitest'
import { dueInfo, DUE_SOON_DAYS } from './dueDate'

// `today` is passed explicitly everywhere so these assertions do not depend on
// when the suite runs. Several cases deliberately use a non-midnight `today`:
// the whole point of this helper is that both ends are compared as CALENDAR
// days, not as instants.

describe('dueInfo', () => {
  it('reports a past due date as overdue with the day count', () => {
    const info = dueInfo('2026-08-17', 'matched', new Date(2026, 7, 20))
    expect(info.tone).toBe('overdue')
    expect(info.days).toBe(-3)
    expect(info.label).toBe('Overdue 3d')
  })

  it('reports a due date landing today as due today', () => {
    const info = dueInfo('2026-08-20', 'matched', new Date(2026, 7, 20))
    expect(info.tone).toBe('soon')
    expect(info.days).toBe(0)
    expect(info.label).toBe('Due today')
  })

  it('reports a due date a few days out as due soon', () => {
    const info = dueInfo('2026-08-23', 'matched', new Date(2026, 7, 20))
    expect(info.tone).toBe('soon')
    expect(info.days).toBe(3)
    expect(info.label).toBe('Due in 3d')
  })

  it('still counts the last day of the warning window as due soon', () => {
    const info = dueInfo('2026-08-25', 'matched', new Date(2026, 7, 20))
    expect(DUE_SOON_DAYS).toBe(5)
    expect(info.days).toBe(5)
    expect(info.tone).toBe('soon')
  })

  it('leaves a due date past the warning window uncoloured', () => {
    const info = dueInfo('2026-08-26', 'matched', new Date(2026, 7, 20))
    expect(info.days).toBe(6)
    expect(info.tone).toBe('normal')
    expect(info.label).toBe('')
  })

  it('never warns on an invoice that is already paid', () => {
    const info = dueInfo('2026-01-05', 'paid', new Date(2026, 7, 20))
    expect(info.tone).toBe('settled')
    expect(info.label).toBe('')
  })

  // Regression: `new Date('2026-08-20')` parses as UTC midnight, which is the
  // PREVIOUS calendar day anywhere west of Greenwich — this company runs at
  // UTC-4/-5 and that bug has already shipped once across the whole app.
  // Comparing a late-evening `today` against the same calendar day fails under
  // a naive implementation in UTC and in UTC-4 alike, so this case pins the
  // behaviour regardless of the machine's zone.
  it('treats a date-only string as a local calendar day, not UTC midnight', () => {
    const lateEvening = new Date(2026, 7, 20, 23, 30)
    expect(dueInfo('2026-08-20', 'matched', lateEvening).days).toBe(0)
    expect(dueInfo('2026-08-21', 'matched', lateEvening).days).toBe(1)
  })

  it('counts across a year boundary', () => {
    const info = dueInfo('2027-01-02', 'matched', new Date(2026, 11, 31))
    expect(info.days).toBe(2)
    expect(info.tone).toBe('soon')
  })

  it('stays neutral when the invoice carries no due date', () => {
    const info = dueInfo(null, 'matched', new Date(2026, 7, 20))
    expect(info.tone).toBe('normal')
    expect(info.days).toBe(null)
    expect(info.label).toBe('')
  })
})
