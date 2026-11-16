import { describe, expect, it } from 'vitest'
import { runConfirmAction } from './confirmAction'

describe('runConfirmAction', () => {
  it('returns null when the action goes through (sync or async)', async () => {
    expect(await runConfirmAction(() => undefined)).toBeNull()
    expect(await runConfirmAction(async () => 'ok')).toBeNull()
  })

  it('returns the refusal message when the action throws', async () => {
    expect(await runConfirmAction(() => { throw new Error('GR is already confirmed') }))
      .toBe('GR is already confirmed')
    expect(await runConfirmAction(async () => { throw new Error('Cannot delete') }))
      .toBe('Cannot delete')
  })

  it('falls back when the thrown value has no message', async () => {
    expect(await runConfirmAction(() => { throw 'boom' }, 'Delete failed')).toBe('Delete failed')
    expect(await runConfirmAction(() => { throw new Error('') })).toBe('The action failed')
  })

  it('waits for the action before reporting success', async () => {
    let done = false
    const result = await runConfirmAction(() => new Promise((r) => setTimeout(() => { done = true; r(null) }, 5)))
    expect(done).toBe(true)
    expect(result).toBeNull()
  })
})
