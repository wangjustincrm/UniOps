import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  displayTimeZone, formatDate, formatDateTime, formatTime, isoDateInZone, loadDisplayTimeZone,
  setDisplayTimeZone, timeZoneAbbrev, todayISO,
} from './date'

afterEach(() => {
  setDisplayTimeZone('America/Toronto')
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('format (UI-DATE-002)', () => {
  it('prints a calendar date as is, whatever the zone', () => {
    setDisplayTimeZone('Asia/Tokyo')
    expect(formatDate('2026-01-01')).toBe('2026-JAN-01')
    expect(formatDate('2026-12-31')).toBe('2026-DEC-31')
    expect(formatDateTime('2026-11-11')).toBe('2026-NOV-11 00:00')
  })

  it('shows an instant on the plant clock, not UTC', () => {
    // 02:30 UTC on 12 Nov = 21:30 EST on 11 Nov.
    expect(formatDate('2026-11-12T02:30:00Z')).toBe('2026-NOV-11')
    expect(formatDateTime('2026-11-12T02:30:00Z')).toBe('2026-NOV-11 21:30')
    expect(formatTime('2026-11-12T02:30:00Z')).toBe('21:30')
    expect(formatDateTime(new Date(Date.UTC(2026, 6, 4, 16, 5)))).toBe('2026-JUL-04 12:05')
  })

  it('follows the company zone', () => {
    setDisplayTimeZone('America/Regina')
    expect(displayTimeZone()).toBe('America/Regina')
    expect(formatDateTime('2026-11-12T02:30:00Z')).toBe('2026-NOV-11 20:30')
  })

  it('midnight prints as 00, not 24', () => {
    expect(formatDateTime('2026-11-11T05:00:00Z')).toBe('2026-NOV-11 00:00')
  })

  it('empty and garbage give a dash', () => {
    expect(formatDate(null)).toBe('—')
    expect(formatDate('')).toBe('—')
    expect(formatDate('garbage')).toBe('—')
    expect(formatDateTime(undefined)).toBe('—')
    expect(formatDate('2026-13-01')).toBe('—')
  })

  it('ignores an offset that is not a zone name', () => {
    setDisplayTimeZone('UTC-5')
    expect(displayTimeZone()).toBe('America/Toronto')
  })

  it('zone abbreviation follows DST', () => {
    expect(timeZoneAbbrev(new Date(Date.UTC(2026, 6, 1)))).toBe('EDT')
    expect(timeZoneAbbrev(new Date(Date.UTC(2026, 11, 1)))).toBe('EST')
  })
})

describe('today', () => {
  it('is the plant date after 19:00 EST, not tomorrow', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-12T01:00:00Z'))   // 20:00 EST on 11 Nov
    expect(todayISO()).toBe('2026-11-11')
    expect(new Date().toISOString().slice(0, 10)).toBe('2026-11-12')   // the old bug
  })

  it('isoDateInZone gives the plant calendar day', () => {
    expect(isoDateInZone(new Date('2026-11-12T04:59:00Z'))).toBe('2026-11-11')
    expect(isoDateInZone(new Date('2026-11-12T05:00:00Z'))).toBe('2026-11-12')
  })
})

describe('loadDisplayTimeZone', () => {
  it('adopts the zone the branding endpoint returns', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ timezone: 'Europe/London' }))))
    await loadDisplayTimeZone('/api/v1/config/public/branding')
    expect(displayTimeZone()).toBe('Europe/London')
  })

  it('keeps the current zone when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
    await loadDisplayTimeZone('/x')
    expect(displayTimeZone()).toBe('America/Toronto')
  })
})
