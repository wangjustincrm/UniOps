import { describe, expect, it } from 'vitest'
import type { InvoiceHistoryEntry } from '@/services/invoices'
import { historyLine, matchTargetLabel } from './invoiceHistory'

const entry = (action: InvoiceHistoryEntry['action'], after: Record<string, unknown> | null,
               before: Record<string, unknown> | null = null): InvoiceHistoryEntry => ({
  id: 'x', action, created_at: '2026-10-28T12:00:00Z', actor_id: 'u', actor_name: 'TEST AP', before, after,
})

describe('invoice history wording (ISS-0170)', () => {
  it('names the assignee', () => {
    expect(historyLine(entry('assign_match', { assignee_name: 'TEST Requester', reassigned: false })).action)
      .toBe('Assigned matching to TEST Requester')
  })

  it('says a delegate match went to review', () => {
    const l = historyLine(entry('match', { po_number: 'PO-400-2610-37', gr_number: null, status: 'match_review' }))
    expect(l.action).toBe('Matched to PO-400-2610-37 — sent to AP for review')
  })

  it('keeps the reject note and names what was rejected', () => {
    const l = historyLine(entry('review_reject', { note: '测试退回,请重新匹配', status: 'unmatched' },
                                { po_number: 'PO-400-2610-37' }))
    expect(l.action).toContain('PO-400-2610-37')
    expect(l.note).toBe('测试退回,请重新匹配')
  })

  it('keeps the confirm note', () => {
    expect(historyLine(entry('review_approve', { note: '确认匹配', status: 'matched' }, { po_number: 'PO-1' })).note)
      .toBe('确认匹配')
  })

  it('shows the unmatch reason and the PO it left', () => {
    const l = historyLine(entry('unmatch', { scope: 'po', reason: '误匹配', status: 'unmatched' },
                                { po_number: 'PO-400-2602-02' }))
    expect(l.action).toBe('Unmatched from PO-400-2602-02 — back to Unmatched')
    expect(l.note).toBe('误匹配')
  })

  it('labels agreement and direct routes', () => {
    expect(matchTargetLabel({ route: 'agreement', agreement_number: 'AGR-1' })).toBe('Agreement AGR-1')
    expect(matchTargetLabel({ route: 'direct' })).toContain('Direct PA')
    expect(matchTargetLabel({ po_number: 'PO-1', gr_number: 'GR-1' })).toBe('PO-1 · GR-1')
  })
})
