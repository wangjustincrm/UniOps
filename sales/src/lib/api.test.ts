// API error parsing (review 2026-10-28): text first, JSON only if it is JSON.
import { describe, expect, it, vi } from 'vitest'
import { api, onSessionExpired, toApiError } from './api'
import { exitToPortal } from './session'

describe('API error parsing', () => {
  it('reads JSON error bodies, keeps the body', async () => {
    const res = new Response(JSON.stringify({ error: { code: 'x', message: 'Nope', details: {} } }), { status: 409 })
    const e = await toApiError(res)
    expect(e.message).toBe('Nope')
    expect(e.body).toEqual({ error: { code: 'x', message: 'Nope', details: {} } })
  })
  it('a non-JSON body and an empty status text still give a readable message', async () => {
    const html = await toApiError(new Response('<html>Bad Gateway</html>', { status: 502, statusText: '' }))
    expect(html.message).toBe('HTTP 502')
    expect(html.body).toBe('<html>Bad Gateway</html>')
    const empty = await toApiError(new Response('', { status: 503, statusText: 'Service Unavailable' }))
    expect(empty.message).toBe('HTTP 503 Service Unavailable')
    const detail = await toApiError(new Response(JSON.stringify({ detail: [{ msg: 'field required' }] }), { status: 422 }))
    expect(detail.message).toBe('field required')
  })
})

describe('a 401 for an account that is not active (user 2026-11-24)', () => {
  it('ends the session and leaves Sales on a page that says so; any other 401 is an expiry as before', async () => {
    sessionStorage.clear()
    const stop = onSessionExpired(() => {})  // a layout is listening: nothing redirects from here
    const body = (code: string) => new Response(JSON.stringify({ error: { code, message: 'x', details: {} } }), { status: 401 })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(body('auth.user_inactive'))
    await expect(api.get('/orders')).rejects.toThrow()
    expect(exitToPortal('http://portal.test', 'http://sales.test/orders')).toEqual({ kind: 'inactive', code: 'auth.user_inactive' })
    sessionStorage.clear()
    fetchMock.mockResolvedValueOnce(body('auth.unauthorized'))
    await expect(api.get('/orders')).rejects.toThrow()
    expect(exitToPortal('http://portal.test', 'http://sales.test/orders')).toEqual({ kind: 'go', url: 'http://portal.test/logout' })
    fetchMock.mockRestore()
    stop()
  })
})

describe('the name a download carries', () => {
  it('filename* (UTF-8) when there is one, else filename, else nothing', async () => {
    const { fileNameOf } = await import('./api')
    expect(fileNameOf("attachment; filename=\"PO-_.pdf\"; filename*=UTF-8''PO-%E8%AE%A2%E5%8D%95.pdf")).toBe('PO-订单.pdf')
    expect(fileNameOf('attachment; filename="orders.csv"')).toBe('orders.csv')
    expect(fileNameOf("attachment; filename=\"x.pdf\"; filename*=UTF-8''%E8%A")).toBe('x.pdf')  // not valid: the plain one
    expect(fileNameOf(null)).toBeUndefined()
  })
})
