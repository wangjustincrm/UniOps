import { describe, expect, it } from 'vitest'
import type { ApiEvent } from '@/services/pr'
import { historyLine, isSkipped } from './approvalHistory'

const ev = (over: Partial<ApiEvent>): ApiEvent => ({
  id: 'e1', step_idx: 1, action: 'approve', actor_id: 'u1', actor_role: 'finance_manager',
  actor_name: 'Kris Enriquez', comment: null, created_at: '2026-10-15T14:00:00Z', kind: 'manual',
  ...over,
})

describe('historyLine (ISS-0077)', () => {
  it('a skipped step names the step and the reason, never the person who triggered it', () => {
    const line = historyLine(ev({
      kind: 'skipped',
      comment: 'Re-synced (config change): Auto-skipped (over-budget approval no longer required)',
      skip_reason: 'over-budget approval no longer required — Re-synced (config change)',
    }))
    expect(line.action).toBe('Skipped')
    expect(line.role).toBe('finance manager step')
    expect(line.actorName).toBeNull()
    expect(line.note).toBe('over-budget approval no longer required — Re-synced (config change)')
  })

  it('a person’s approval keeps their name and comment', () => {
    const line = historyLine(ev({ actor_role: 'director', actor_name: 'Farshid Mohammadi', comment: 'ok' }))
    expect(line).toEqual({ action: 'approve', role: 'by director', actorName: 'Farshid Mohammadi', note: 'ok' })
  })

  it('a same-approver auto-approval is still that person', () => {
    const line = historyLine(ev({ kind: 'same_approver', comment: 'Auto-approved (same approver holds both roles)' }))
    expect(line.actorName).toBe('Kris Enriquez')
  })

  it('a PMS-import backfill does not credit the import account', () => {
    const line = historyLine(ev({ kind: 'imported', actor_name: 'System Admin',
      comment: 'Auto-approved on behalf of Finance Manager — PMS migration' }))
    expect(line.actorName).toBeNull()
    expect(line.role).toContain('PMS import')
  })

  it('isSkipped reads kind, not comment text', () => {
    expect(isSkipped(ev({ kind: 'skipped' }))).toBe(true)
    // The page trusts the server's kind and never re-reads the comment text.
    expect(isSkipped(ev({ comment: 'Auto-skipped? no, approved' }))).toBe(false)
    expect(isSkipped(undefined)).toBe(false)
  })
})
