import { describe, expect, it } from 'vitest'
import { formatDuration, formatNumber, timeFromNow } from './format'

const NOW = new Date(2026, 10, 6, 12, 0).getTime()

describe('time from now', () => {
  it('names the two largest units', () => {
    expect(formatDuration(30_000)).toBe('under 1m')
    expect(formatDuration(12 * 60_000)).toBe('12m')
    expect(formatDuration(153 * 60_000)).toBe('2h 33m')
    expect(formatDuration(76 * 3_600_000)).toBe('3d 4h')
  })
  it('a moment is before or after now', () => {
    expect(timeFromNow(new Date(NOW + 153 * 60_000).toISOString(), NOW)).toEqual({ text: 'in 2h 33m', past: false })
    expect(timeFromNow(new Date(NOW - 76 * 3_600_000).toISOString(), NOW)).toEqual({ text: '3d 4h ago', past: true })
  })
  it('a calendar day stays that day in any time zone', () => {
    expect(timeFromNow('2026-11-06', NOW)).toEqual({ text: 'today', past: false })
    expect(timeFromNow('2026-11-18', NOW)).toEqual({ text: 'in 12 days', past: false })
    expect(timeFromNow('2026-11-05', NOW)).toEqual({ text: '1 day ago', past: true })
  })
  it('no value, no text', () => {
    expect(timeFromNow(null, NOW)).toBeNull()
    expect(formatNumber(null)).toBe('')
    expect(formatNumber('1200')).toBe('1,200')
  })
})
