import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NoticeScope } from '@/platform-ui'
import { noticeIn } from '@/platform-ui/testing'
import { runNotice } from './followRun'
import type { RunRead } from './operationsApi'
import { useRunFollower } from './useRunFollower'

const h = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('./operationsApi', async (orig) => {
  const real = await orig<typeof import('./operationsApi')>()
  return { ...real, runsClient: { ...real.runsClient, get: h.get } }
})

const run = (over: Partial<RunRead>) => ({ id: 12, status: 'SUCCEEDED', summary: { text: '3 files, 0 errors' }, error: null, ...over }) as RunRead

const mount = () => renderHook(() => useRunFollower(), { wrapper: NoticeScope }).result

beforeEach(() => { h.get.mockReset() })

describe('useRunFollower', () => {
  it('says the run is going, follows it, and says how it ended in the same place', async () => {
    let finish: (r: RunRead) => void = () => {}
    h.get.mockImplementation(() => new Promise((r) => { finish = r }))
    const follower = mount()
    let ended: Promise<RunRead>
    act(() => { ended = follower.current.run(async () => ({ run_id: 12 })) })
    await waitFor(() => expect(noticeIn()).toHaveTextContent('Run 12 is running…'))
    expect(follower.current.running).toBe(true)
    await act(async () => { finish(run({})); await ended })
    expect(noticeIn()).toHaveTextContent('Run 12: 3 files, 0 errors')
    expect(follower.current.running).toBe(false)
  })

  it('a refusal to start is the caller\'s to word: thrown, nothing said', async () => {
    const follower = mount()
    await act(async () => { await expect(follower.current.run(async () => { throw new Error('refused') })).rejects.toThrow('refused') })
    expect(noticeIn()).toHaveTextContent('')
    expect(h.get).not.toHaveBeenCalled()
  })

  it('losing the run while following clears "running" and throws', async () => {
    h.get.mockRejectedValue(new Error('gone'))
    const follower = mount()
    await act(async () => { await expect(follower.current.run(async () => ({ run_id: 12 }))).rejects.toThrow('gone') })
    expect(noticeIn()).toHaveTextContent('')
    expect(follower.current.running).toBe(false)
  })
})

describe('runNotice', () => {
  it('a success by its summary; otherwise how it ended, in its tone', () => {
    expect(runNotice(run({}))).toEqual({ tone: 'success', text: 'Run 12: 3 files, 0 errors' })
    expect(runNotice(run({ status: 'PARTIAL' }))).toEqual({ tone: 'warning', text: 'Run 12: PARTIAL — 3 files, 0 errors' })
    expect(runNotice(run({ status: 'FAILED', summary: null, error: 'timed out' }))).toEqual({ tone: 'error', text: 'Run 12: FAILED — timed out' })
    expect(runNotice(run({ status: 'RUNNING' }))).toEqual({ tone: 'info', text: 'Run 12 is still running' })
  })
})
