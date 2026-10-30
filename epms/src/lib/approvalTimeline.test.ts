import { describe, expect, it } from 'vitest'
import type { ApiEvent } from '@/services/pr'
import { approvalNodeStates, buildApprovalNodes } from './approvalTimeline'

let seq = 0
const ev = (over: Partial<ApiEvent>): ApiEvent => ({
  id: `e${++seq}`, step_idx: 0, action: 'approve', actor_id: 'u1', actor_role: 'dept_manager',
  actor_name: 'Dana Manager', comment: null, created_at: `2026-10-26T10:00:${String(seq).padStart(2, '0')}Z`,
  kind: 'manual',
  ...over,
})

const NODES = [
  { id: 'dm', label: 'Department Manager' },
  { id: 'dir', label: 'Director' },
  { id: 'gm', label: 'GM / OPM' },
]
const PR_DONE = ['approved', 'paid', 'closed']
const build = (status: string, stepIdx: number, events: ApiEvent[], currentStep = null as never) =>
  buildApprovalNodes({ nodes: NODES, status, stepIdx, events, currentStep, doneStatuses: PR_DONE, ownerLabel: 'requester' })

describe('approval timeline (ISS-0084, ISS-0085, ISS-0088)', () => {
  it('a skipped step carries its reason and no name', () => {
    const steps = build('in_review', 2, [
      ev({ action: 'submit', actor_name: 'Rita Requester' }),
      ev({ step_idx: 0 }),
      ev({ step_idx: 1, actor_name: 'System Administrator', kind: 'skipped',
           comment: 'Auto-skipped (department has no Director)', skip_reason: 'department has no Director' }),
    ])
    expect(steps[1]).toMatchObject({ status: 'skipped', note: 'department has no Director' })
    expect(steps[1].actorName).toBeUndefined()
  })

  it('a submitted document marks its first step current and names who holds it', () => {
    const steps = build('submitted', 0, [ev({ action: 'submit' })], {
      role: 'dept_manager', label: 'Dept Manager',
      approver_name: 'TEST Dept Manager (delegated: TEST Payment Officer)', since: new Date().toISOString(),
    } as never)
    expect(steps.map((s) => s.status)).toEqual(['current', 'pending', 'pending'])
    expect(steps[0].actorName).toBe('TEST Dept Manager (delegated: TEST Payment Officer)')
    expect(steps[0].daysWaiting).toBe(0)
  })

  it('after a recall the earlier approval no longer names the waiting step', () => {
    const events = [
      ev({ action: 'submit' }),
      ev({ step_idx: 0, actor_name: 'System Administrator' }),
      ev({ step_idx: 1, action: 'recall', actor_name: 'TEST Requester' }),
    ]
    // Back in draft: nothing is current, nothing is named.
    expect(build('draft', 0, events).map((s) => [s.status, s.actorName]))
      .toEqual([['pending', undefined], ['pending', undefined], ['pending', undefined]])
    // Resubmitted: waiting at step 0 on the open task's holder, not the old approver.
    const steps = build('submitted', 0, [...events, ev({ action: 'submit' })],
      { role: 'dept_manager', label: 'Dept Manager', approver_name: 'TEST Dept Manager', since: new Date().toISOString() } as never)
    expect(steps[0]).toMatchObject({ status: 'current', actorName: 'TEST Dept Manager' })
  })

  it('a returned document keeps the steps it passed and marks the step that returned it', () => {
    const steps = build('returned', 0, [
      ev({ action: 'submit' }),
      ev({ step_idx: 0, actor_name: 'Pat Manager' }),
      ev({ step_idx: 1, actor_name: 'Dee Director' }),
      ev({ step_idx: 2, action: 'return', actor_name: 'Gil GM', comment: 'Fix the quantity' }),
    ])
    expect(steps.map((s) => s.status)).toEqual(['completed', 'completed', 'returned'])
    expect(steps[0].actorName).toBe('Pat Manager')
    expect(steps[2]).toMatchObject({ action: 'Returned', actorName: 'Gil GM', comment: 'Fix the quantity' })
    expect(steps[2].note).toContain('waiting for the requester')
  })

  it('returned twice: the latest return counts and re-approvals replace earlier ones', () => {
    const steps = build('returned', 0, [
      ev({ action: 'submit' }),
      ev({ step_idx: 0, actor_name: 'Round1' }),
      ev({ step_idx: 1, action: 'return', actor_name: 'Dee Director' }),
      ev({ action: 'submit' }),
      ev({ step_idx: 1, actor_name: 'Dee Director' }),
      ev({ step_idx: 2, action: 'return', actor_name: 'Gil GM' }),
    ])
    expect(steps.map((s) => s.status)).toEqual(['completed', 'completed', 'returned'])
    expect(steps[1].actorName).toBe('Dee Director')
  })

  it('a rejected document marks the rejecting step, not a skip', () => {
    const steps = build('rejected', 0, [
      ev({ action: 'submit' }),
      ev({ step_idx: 0, action: 'reject', actor_name: 'Pat Manager', comment: 'No budget' }),
    ])
    expect(steps.map((s) => s.status)).toEqual(['rejected', 'pending', 'pending'])
    expect(steps[0]).toMatchObject({ action: 'Rejected', actorName: 'Pat Manager', comment: 'No budget' })
  })

  it('PA records a reject as cancelled; it still reads as rejected at that step', () => {
    const states = approvalNodeStates(3, 'cancelled', 1, [
      ev({ action: 'submit' }), ev({ step_idx: 0 }), ev({ step_idx: 1, action: 'reject' }),
    ], ['approved', 'processed'])
    expect(states.map((s) => s.status)).toEqual(['completed', 'rejected', 'pending'])
  })

  it('a cancel is nobody’s decision at a step: no skip, no marker', () => {
    const steps = build('cancelled', 0, [ev({ action: 'submit' }), ev({ step_idx: 0, action: 'cancel' })])
    expect(steps.map((s) => s.status)).toEqual(['pending', 'pending', 'pending'])
  })

  it('a finished document shows every step passed, skips kept', () => {
    const steps = build('approved', 3, [
      ev({ step_idx: 0 }),
      ev({ step_idx: 1, kind: 'skipped', skip_reason: 'department has no Director' }),
      ev({ step_idx: 2, actor_name: 'Gil GM' }),
    ])
    expect(steps.map((s) => s.status)).toEqual(['completed', 'skipped', 'completed'])
  })
})
