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

describe('historyLine names the step for a return / reject too (ISS-0183)', () => {
  const steps = [
    { id: 'dept_manager', role: 'dept_manager', label: 'Department Manager' },
    { id: 'director', role: 'director', label: 'Director' },
  ]

  it('a system admin rejecting at the department-manager step reads as that step', () => {
    const line = historyLine(ev({ step_idx: 0, action: 'reject', actor_role: 'system_admin', actor_name: 'System Administrator' }), steps)
    expect(line.role).toBe('by Department Manager')
    expect(line.actorName).toBe('System Administrator')
  })

  it('a return names its step the same way', () => {
    expect(historyLine(ev({ step_idx: 1, action: 'return', actor_role: 'system_admin' }), steps).role).toBe('by Director')
  })

  it('a submit keeps the actor role (it is not a workflow step)', () => {
    expect(historyLine(ev({ step_idx: 0, action: 'submit', actor_role: 'system_admin' }), steps).role).toBe('by system admin')
  })

  it('without the workflow steps it falls back to the recorded role', () => {
    expect(historyLine(ev({ step_idx: 0, action: 'reject', actor_role: 'system_admin' })).role).toBe('by system admin')
  })
})
