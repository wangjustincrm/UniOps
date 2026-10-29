// API error parsing (review 2026-10-28): text first, JSON only if it is JSON.
import { describe, expect, it } from 'vitest'
import { toApiError } from './api'

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
