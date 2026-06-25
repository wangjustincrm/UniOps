import { describe, it, expect } from 'vitest'
import { resolveRoute, deriveTabMeta } from './routeTable'
import type { RouteDef } from './types'

const routes: RouteDef[] = [
  { path: '/dashboard', element: null, tab: { title: 'Dashboard', icon: 'LayoutDashboard', keyStrategy: 'static', pinned: true, closable: false } },
  { path: '/all', element: null, tab: { title: 'All Visits', icon: 'ListChecks', keyStrategy: 'static' } },
  { path: '/visit/:visitId', element: null, tab: { title: (p) => `Visit ${p.visitId}`, keyStrategy: 'param', paramName: 'visitId' } },
  { path: '/login', element: null }, // not tabbable
]

describe('resolveRoute', () => {
  it('matches a static route', () => {
    const r = resolveRoute(routes, '/all')
    expect(r?.def.path).toBe('/all')
    expect(r?.params).toEqual({})
  })
  it('matches a param route and extracts params', () => {
    const r = resolveRoute(routes, '/visit/123')
    expect(r?.def.path).toBe('/visit/:visitId')
    expect(r?.params.visitId).toBe('123')
  })
  it('returns null for an unknown path', () => {
    expect(resolveRoute(routes, '/nope/x')).toBeNull()
  })
})

describe('deriveTabMeta', () => {
  it('keys static routes by route path', () => {
    expect(deriveTabMeta(routes, '/all')?.key).toBe('/all')
  })
  it('keys param routes by route path + param value', () => {
    expect(deriveTabMeta(routes, '/visit/123')?.key).toBe('/visit/:visitId:123')
    expect(deriveTabMeta(routes, '/visit/456')?.key).toBe('/visit/:visitId:456')
  })
  it('resolves a function title with params', () => {
    expect(deriveTabMeta(routes, '/visit/123')?.title).toBe('Visit 123')
  })
  it('carries pinned + closable flags', () => {
    const m = deriveTabMeta(routes, '/dashboard')!
    expect(m.pinned).toBe(true)
    expect(m.closable).toBe(false)
  })
  it('defaults closable to true', () => {
    expect(deriveTabMeta(routes, '/all')?.closable).toBe(true)
  })
  it('returns null for non-tabbable routes', () => {
    expect(deriveTabMeta(routes, '/login')).toBeNull()
  })
  it('sets path to the concrete pathname', () => {
    expect(deriveTabMeta(routes, '/visit/123')?.path).toBe('/visit/123')
  })
})
