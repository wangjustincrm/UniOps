import { describe, it, expect, beforeEach } from 'vitest'
import { createTabStore } from './tabStore'
import type { TabMeta } from './types'

const dash: TabMeta = { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', pinned: true, closable: false }
const page = (key: string): TabMeta => ({ key, title: key, kind: 'page', path: key, closable: true })

function fresh() {
  localStorage.clear()
  return createTabStore({ storageKey: 'test:tabs', initialTabs: [dash] })
}

describe('tab store', () => {
  let store: ReturnType<typeof createTabStore>
  beforeEach(() => { store = fresh() })

  it('seeds with the pinned initial tab as active', () => {
    const s = store.getState()
    expect(s.tabs.map(t => t.key)).toEqual(['/dashboard'])
    expect(s.activeKey).toBe('/dashboard')
    expect(s.alive).toContain('/dashboard')
  })

  it('opens a new tab and makes it active + alive', () => {
    store.getState().openTab(page('/all'))
    const s = store.getState()
    expect(s.tabs.map(t => t.key)).toEqual(['/dashboard', '/all'])
    expect(s.activeKey).toBe('/all')
    expect(s.alive).toContain('/all')
  })

  it('dedups by key — re-opening focuses the existing tab', () => {
    store.getState().openTab(page('/all'))
    store.getState().openTab(page('/active'))
    store.getState().openTab(page('/all'))
    const s = store.getState()
    expect(s.tabs.filter(t => t.key === '/all')).toHaveLength(1)
    expect(s.activeKey).toBe('/all')
  })

  it('closes a tab and activates the right neighbour', () => {
    store.getState().openTab(page('/a'))
    store.getState().openTab(page('/b'))
    store.getState().openTab(page('/c'))
    store.getState().setActive('/b')
    store.getState().closeTab('/b')
    const s = store.getState()
    expect(s.tabs.map(t => t.key)).toEqual(['/dashboard', '/a', '/c'])
    expect(s.activeKey).toBe('/c') // right neighbour
    expect(s.alive).not.toContain('/b')
  })

  it('falls back to left neighbour when closing the last tab', () => {
    store.getState().openTab(page('/a'))
    store.getState().openTab(page('/b'))
    store.getState().closeTab('/b')
    expect(store.getState().activeKey).toBe('/a')
  })

  it('refuses to close a pinned tab', () => {
    store.getState().closeTab('/dashboard')
    expect(store.getState().tabs.map(t => t.key)).toEqual(['/dashboard'])
  })

  it('closeOthers keeps the target + pinned', () => {
    store.getState().openTab(page('/a'))
    store.getState().openTab(page('/b'))
    store.getState().closeOthers('/a')
    expect(store.getState().tabs.map(t => t.key)).toEqual(['/dashboard', '/a'])
  })

  it('closeAll keeps only pinned and activates it', () => {
    store.getState().openTab(page('/a'))
    store.getState().openTab(page('/b'))
    store.getState().closeAll()
    const s = store.getState()
    expect(s.tabs.map(t => t.key)).toEqual(['/dashboard'])
    expect(s.activeKey).toBe('/dashboard')
  })

  it('updateTitle changes the tab title', () => {
    store.getState().openTab(page('/visit/1'))
    store.getState().updateTitle('/visit/1', 'Visit ACME')
    expect(store.getState().tabs.find(t => t.key === '/visit/1')?.title).toBe('Visit ACME')
  })

  it('setDirty flags + clears a tab', () => {
    store.getState().openTab(page('/new'))
    store.getState().setDirty('/new', true)
    expect(store.getState().tabs.find(t => t.key === '/new')?.dirty).toBe(true)
    store.getState().setDirty('/new', false)
    expect(store.getState().tabs.find(t => t.key === '/new')?.dirty).toBe(false)
  })
})

describe('tab store — eviction (cap)', () => {
  const dash2: TabMeta = { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', pinned: true, closable: false }
  const pg = (k: string): TabMeta => ({ key: k, title: k, kind: 'page', path: k, closable: true })

  function freshCapped(cap: number) {
    localStorage.clear()
    return createTabStore({ storageKey: 'test:cap', cap, initialTabs: [dash2] })
  }

  it('evicts the least-recently-used non-pinned tab when over cap', () => {
    const store = freshCapped(3) // cap counts page tabs incl. pinned
    store.getState().openTab(pg('/a'))
    store.getState().openTab(pg('/b'))     // tabs: dash,a,b (=3, at cap)
    store.getState().setActive('/a')        // lru: dash,b,a  → b is now LRU non-pinned
    store.getState().openTab(pg('/c'))      // over cap → evict b
    expect(store.getState().tabs.map(t => t.key)).toEqual(['/dashboard', '/a', '/c'])
  })

  it('never evicts pinned tabs', () => {
    const store = freshCapped(2)
    store.getState().openTab(pg('/a')) // dash,a (cap 2)
    store.getState().openTab(pg('/b')) // over cap → evict a (not dash)
    expect(store.getState().tabs.map(t => t.key)).toEqual(['/dashboard', '/b'])
  })

  it('skips dirty tabs when choosing an eviction victim', () => {
    const store = freshCapped(3)
    store.getState().openTab(pg('/a'))
    store.getState().openTab(pg('/b'))
    store.getState().setDirty('/a', true) // a is oldest non-pinned but dirty
    store.getState().setActive('/b')
    store.getState().openTab(pg('/c'))     // should evict b-area LRU, skipping dirty /a
    const keys = store.getState().tabs.map(t => t.key)
    expect(keys).toContain('/a')           // dirty preserved
    expect(keys).toContain('/dashboard')
  })
})
