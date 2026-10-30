import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BOUNCE_WINDOW_MS, MAX_BOUNCES, clearSessionMarks, exitToPortal, leaveFor, markSessionExpired } from './session'

// The Sales ↔ Portal loop on an expired session (dev2, 2026-10-30): a 401
// must lead to the Portal's /logout (never back through ?returnUrl, which
// hands the same expired token back), and repeated bounces must stop.

const PORTAL = 'http://portal.test'
const HERE = 'http://sales.test/config/partners'

describe('leaving for the Portal', () => {
  beforeEach(() => { sessionStorage.clear(); localStorage.clear() })

  it('without a session it asks the Portal to sign in and come back here', () => {
    expect(exitToPortal(PORTAL, HERE, 1000)).toEqual({ kind: 'go', url: `${PORTAL}?returnUrl=${encodeURIComponent(HERE)}` })
  })

  it('after a 401 it goes to /logout, so the Portal drops the expired session too', () => {
    localStorage.setItem('sales-auth', '{"state":{"token":"old"}}')
    localStorage.setItem('portal-auth', '{"state":{"token":"old"}}')
    markSessionExpired(1000)
    expect(localStorage.getItem('sales-auth')).toBeNull()
    expect(localStorage.getItem('portal-auth')).toBeNull()  // a stale handoff copy cannot bring it back
    expect(exitToPortal(PORTAL, HERE, 2000)).toEqual({ kind: 'go', url: `${PORTAL}/logout` })
    clearSessionMarks()  // a new session from the Portal
    expect(exitToPortal(PORTAL, HERE, 3000).kind === 'go' && exitToPortal(PORTAL, HERE, 3000)).toEqual({ kind: 'go', url: `${PORTAL}?returnUrl=${encodeURIComponent(HERE)}` })
  })

  it(`stops after ${MAX_BOUNCES} bounces in ${BOUNCE_WINDOW_MS / 1000} s instead of looping`, () => {
    const href = vi.spyOn(window, 'location', 'get').mockReturnValue({ href: '' } as Location)
    for (let i = 0; i < MAX_BOUNCES; i++) {
      const next = exitToPortal(PORTAL, HERE, 1000 + i * 1000)
      expect(next.kind).toBe('go')
      if (next.kind === 'go') leaveFor(next.url, 1000 + i * 1000)
    }
    href.mockRestore()
    expect(exitToPortal(PORTAL, HERE, 4500)).toEqual({ kind: 'stop' })
    expect(exitToPortal(PORTAL, HERE, 4500 + BOUNCE_WINDOW_MS).kind).toBe('go')  // later: allowed again
  })
})

describe('a 401 from the API', () => {
  const realFetch = globalThis.fetch
  afterEach(() => { globalThis.fetch = realFetch; vi.resetModules() })

  it('marks the session expired and tells the layout, which then leaves through /logout', async () => {
    sessionStorage.clear()
    localStorage.setItem('sales-auth', JSON.stringify({ state: { token: 'old' } }))
    globalThis.fetch = vi.fn(async () => new Response('{"detail":"expired"}', { status: 401 })) as typeof fetch
    const { api, onSessionExpired, PORTAL_URL } = await import('./api')
    const told = vi.fn()
    const off = onSessionExpired(told)
    await expect(api.get('/orders')).rejects.toThrow(/Session expired/)
    off()
    expect(told).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('sales-auth')).toBeNull()
    expect(exitToPortal(PORTAL_URL, HERE)).toEqual({ kind: 'go', url: `${PORTAL_URL}/logout` })
  })
})
