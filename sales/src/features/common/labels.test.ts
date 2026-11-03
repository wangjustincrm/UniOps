import { describe, expect, it } from 'vitest'
import { formatDate, formatDateTime } from '@/platform-ui'
import { partnerLabel } from './labels'

describe('one way to say a partner and a date (polish, 2026-11-03)', () => {
  it('a partner whose name is its code is said once', () => {
    expect(partnerLabel('Shopify', 'Shopify')).toBe('Shopify')
    expect(partnerLabel('Manual', 'MANUAL')).toBe('Manual')
    expect(partnerLabel('Loblaws', 'AC-9898')).toBe('Loblaws (AC-9898)')
    expect(partnerLabel(null, 'WMT')).toBe('WMT')
  })

  it('a day reads like a date-time\'s date, and never moves with the time zone', () => {
    expect(formatDate('2026-11-18')).toBe('Nov 18, 2026')
    expect(formatDate('2026-01-01')).toBe('Jan 1, 2026')  // not Dec 31 west of Greenwich
    expect(formatDateTime('2026-11-18')).toBe('Nov 18, 2026')
    expect(formatDate(null)).toBe('—')
  })
})
