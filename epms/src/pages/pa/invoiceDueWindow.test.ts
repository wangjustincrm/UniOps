import { describe, expect, it } from 'vitest'
import { PA_INVOICE_DUE_WINDOW_DAYS, dueWindowViolation, earliestDueGroup } from './invoiceDueWindow'

const inv = (ref: string, due: string) => ({ internal_ref: ref, due_date: due })

describe('dueWindowViolation', () => {
  it('matches the server window', () => {
    expect(PA_INVOICE_DUE_WINDOW_DAYS).toBe(7)
  })

  it('accepts a single invoice', () => {
    expect(dueWindowViolation([inv('A', '2026-03-02')])).toBeNull()
  })

  it('accepts invoices exactly a week apart', () => {
    expect(dueWindowViolation([inv('A', '2026-03-02'), inv('B', '2026-03-09')])).toBeNull()
  })

  it('refuses invoices eight days apart and names both ends', () => {
    const v = dueWindowViolation([inv('B', '2026-03-05'), inv('A', '2026-03-02'), inv('C', '2026-03-10')])
    expect(v).not.toBeNull()
    expect(v!.earliest.internal_ref).toBe('A')
    expect(v!.latest.internal_ref).toBe('C')
    expect(v!.spanDays).toBe(8)
  })

  it('counts across a DST change and a month end without drift', () => {
    expect(dueWindowViolation([inv('A', '2026-03-03'), inv('B', '2026-03-10')])).toBeNull()
    expect(dueWindowViolation([inv('A', '2026-01-28'), inv('B', '2026-02-05')])?.spanDays).toBe(8)
  })
})

describe('earliestDueGroup', () => {
  it('keeps only the invoices due within a week of the earliest', () => {
    const group = earliestDueGroup([
      inv('late', '2026-04-01'), inv('first', '2026-03-02'), inv('edge', '2026-03-09'), inv('out', '2026-03-10'),
    ])
    expect(group.map((i) => i.internal_ref)).toEqual(['first', 'edge'])
    expect(dueWindowViolation(group)).toBeNull()
  })

  it('is empty for no candidates', () => {
    expect(earliestDueGroup([])).toEqual([])
  })
})
