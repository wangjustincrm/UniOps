import { describe, expect, it } from 'vitest'
import { holdsApproveTask } from './stepHolder'

const task = (assigned_role: string, assigned_user_id: string | null, document_id = 'pr-1', type = 'approve_pr') =>
  ({ document_id, type, assigned_role, assigned_user_id })
const admin = { id: 'admin-1', role: 'system_admin' }

describe('holdsApproveTask (ISS-0156)', () => {
  it("a System Admin does not hold another user's step", () => {
    expect(holdsApproveTask([task('dept_manager', 'mgr-1')], 'pr-1', 'approve_pr', admin, ['system_admin'])).toBe(false)
  })

  it('a System Admin does not hold a pool step of a role they lack', () => {
    expect(holdsApproveTask([task('finance_manager', null)], 'pr-1', 'approve_pr', admin, ['system_admin'])).toBe(false)
  })

  it('a System Admin holds a step assigned to them by name', () => {
    expect(holdsApproveTask([task('dept_manager', 'admin-1')], 'pr-1', 'approve_pr', admin)).toBe(true)
  })

  it('a System Admin holds a pool step of an additional role they hold', () => {
    expect(holdsApproveTask([task('finance_manager', null)], 'pr-1', 'approve_pr', admin, ['system_admin', 'finance_manager'])).toBe(true)
  })

  it('an unassigned Director task is not a pool', () => {
    expect(holdsApproveTask([task('director', null)], 'pr-1', 'approve_pr', admin, ['director'])).toBe(false)
  })

  it('other documents and task types do not count', () => {
    const tasks = [task('dept_manager', 'admin-1', 'pr-2'), task('dept_manager', 'admin-1', 'pr-1', 'revise_pr')]
    expect(holdsApproveTask(tasks, 'pr-1', 'approve_pr', admin)).toBe(false)
  })

  it('anyone else: the server already scoped the list (roles, delegation)', () => {
    const viewer = { id: 'deleg-1', role: 'dept_manager' }
    expect(holdsApproveTask([task('dept_manager', 'mgr-1')], 'pr-1', 'approve_pr', viewer)).toBe(true)
    expect(holdsApproveTask([], 'pr-1', 'approve_pr', viewer)).toBe(false)
  })
})
