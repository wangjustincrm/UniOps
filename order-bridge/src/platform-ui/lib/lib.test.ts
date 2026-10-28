import { describe, expect, it, vi } from 'vitest'
import { toSearchParams } from './querySpec'
import { businessErrorOf, errorText } from './businessError'
import { createResourceClient, type HttpClient } from '../resource/ResourceClient'

describe('toSearchParams', () => {
  it('speaks the back end list contract', () => {
    const p = toSearchParams({
      page: 2, pageSize: 500, sort: [{ field: 'code' }, { field: 'price', desc: true }], search: '  ab ',
      filters: [
        { field: 'code', op: 'in', value: ['A', 'B'] },
        { field: 'price', op: 'range', value: [null, '5'] },
        { field: 'active', op: 'eq', value: 'true' },
      ],
      includeHistorical: true,
    })
    expect(p.get('page')).toBe('2')
    expect(p.get('page_size')).toBe('200') // capped at the server maximum
    expect(p.get('sort')).toBe('code,-price')
    expect(p.get('search')).toBe('ab')
    expect(p.getAll('filter')).toEqual(['code:in:A|B', 'price:range:..5', 'active:eq:true'])
    expect(p.get('include_historical')).toBe('true')
  })
  it('omits what is not set', () => {
    expect(toSearchParams({ page: 1, pageSize: 50 }).toString()).toBe('page=1&page_size=50')
  })
})

describe('businessErrorOf', () => {
  const body = { error: { code: 'entity.stale', message: 'changed', details: { field: 'x' } } }
  it('reads the error from body, detail or the object itself', () => {
    expect(businessErrorOf({ body })?.code).toBe('entity.stale')
    expect(businessErrorOf({ detail: body })?.details).toEqual({ field: 'x' })
    expect(businessErrorOf(body)?.message).toBe('changed')
    expect(businessErrorOf(new Error('x'))).toBeNull()
    expect(businessErrorOf({ detail: 'Insufficient permissions' })).toBeNull()
  })
  it('maps codes to module text, falling back to the server message', () => {
    expect(errorText({ body }, { 'entity.stale': 'Reload first' })).toBe('Reload first')
    expect(errorText({ body })).toBe('changed')
    expect(errorText(new Error('net down'))).toBe('net down')
  })
})

describe('createResourceClient', () => {
  const http: HttpClient = {
    get: vi.fn(async () => ({ items: [], total: 0 })) as HttpClient['get'],
    post: vi.fn(async () => ({})) as HttpClient['post'],
    put: vi.fn(async () => ({})) as HttpClient['put'],
    upload: vi.fn(async () => ({ ok: true })) as NonNullable<HttpClient['upload']>,
  }
  const c = createResourceClient(http, '/config/things/')

  it('builds the crud_router paths', async () => {
    await c.list({ page: 1, pageSize: 20, sort: [{ field: 'name' }] })
    expect(http.get).toHaveBeenCalledWith('/config/things?page=1&page_size=20&sort=name')
    await c.update(3, { email: null }, 7)
    expect(http.put).toHaveBeenCalledWith('/config/things/3?version=7', { email: null })
    await c.deactivate(3)
    expect(http.post).toHaveBeenCalledWith('/config/things/3/deactivate')
    expect(c.exportPath({ page: 4, pageSize: 20, search: 'x' })).toBe('/config/things/export.csv?search=x')
    await c.importCsv(new File(['code\n'], 'a.csv'), true)
    expect(http.upload).toHaveBeenCalledWith('/config/things/import.csv?dry_run=true', expect.any(FormData))
  })
  it('refuses an import when the client cannot upload', () => {
    const noUpload = createResourceClient({ ...http, upload: undefined }, '/x')
    expect(() => noUpload.importCsv(new File([''], 'a.csv'), true)).toThrow(/cannot upload/)
  })
})
