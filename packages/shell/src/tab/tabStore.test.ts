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

describe('tab store — persistence', () => {
  const dash3: TabMeta = { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', pinned: true, closable: false }
  const pg = (k: string): TabMeta => ({ key: k, title: k, kind: 'page', path: k, closable: true })

  it('persists open tabs + activeKey to localStorage', () => {
    localStorage.clear()
    const a = createTabStore({ storageKey: 'persist:tabs', initialTabs: [dash3] })
    a.getState().openTab(pg('/all'))
    a.getState().openTab(pg('/active'))
    a.getState().setActive('/all')

    // New store instance with the same storageKey rehydrates prior tabs.
    const b = createTabStore({ storageKey: 'persist:tabs', initialTabs: [dash3] })
    const s = b.getState()
    expect(s.tabs.map(t => t.key)).toEqual(['/dashboard', '/all', '/active'])
    expect(s.activeKey).toBe('/all')
  })

  it('on restore, only the active tab is alive (others lazy-mount)', () => {
    localStorage.clear()
    const a = createTabStore({ storageKey: 'persist:alive', initialTabs: [dash3] })
    a.getState().openTab(pg('/all'))
    a.getState().openTab(pg('/active'))
    a.getState().setActive('/active')

    const b = createTabStore({ storageKey: 'persist:alive', initialTabs: [dash3] })
    expect(b.getState().alive).toEqual(['/active'])
  })

  it('re-injects the pinned tab if storage somehow lacks it', () => {
    localStorage.setItem('persist:nopin', JSON.stringify({
      state: { tabs: [{ key: '/all', title: 'All', kind: 'page', path: '/all', closable: true }], activeKey: '/all' },
      version: 0,
    }))
    const b = createTabStore({ storageKey: 'persist:nopin', initialTabs: [dash3] })
    expect(b.getState().tabs.some(t => t.key === '/dashboard')).toBe(true)
  })

  it('repairs stale pinned-tab flags on restore (pinned cannot be closed)', () => {
    // Persisted storage has a /dashboard entry with stale, non-pinned flags —
    // simulating data persisted before it was configured as a pinned initial tab.
    localStorage.setItem('persist:stalepin', JSON.stringify({
      state: {
        tabs: [{ key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', pinned: false, closable: true }],
        activeKey: '/dashboard',
      },
      version: 0,
    }))
    const b = createTabStore({ storageKey: 'persist:stalepin', initialTabs: [dash3] })
    const restored = b.getState().tabs.find(t => t.key === '/dashboard')
    expect(restored?.pinned).toBe(true)
    expect(restored?.closable).toBe(false)
  })
})

describe('tab store — replaceTab', () => {
  const dashR: TabMeta = { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', pinned: true, closable: false }
  const pg = (k: string): TabMeta => ({ key: k, title: k, kind: 'page', path: k, closable: true })
  function freshR() { localStorage.clear(); return createTabStore({ storageKey: 'test:replace', initialTabs: [dashR] }) }

  it('replaces a tab in place, keeping position and activating the target', () => {
    const s = freshR()
    s.getState().openTab(pg('/new'))
    s.getState().replaceTab('/new', pg('/visit/1'))
    const st = s.getState()
    expect(st.tabs.map(t => t.key)).toEqual(['/dashboard', '/visit/1'])
    expect(st.activeKey).toBe('/visit/1')
    expect(st.alive).toContain('/visit/1')
    expect(st.alive).not.toContain('/new')
  })

  it('drops the old tab and focuses the destination when it is already open', () => {
    const s = freshR()
    s.getState().openTab(pg('/visit/1'))
    s.getState().openTab(pg('/new'))
    s.getState().replaceTab('/new', pg('/visit/1'))
    const st = s.getState()
    expect(st.tabs.map(t => t.key)).toEqual(['/dashboard', '/visit/1'])
    expect(st.activeKey).toBe('/visit/1')
  })

  it('falls back to opening the target when the old tab is pinned', () => {
    const s = freshR()
    s.getState().replaceTab('/dashboard', pg('/visit/1'))
    const st = s.getState()
    expect(st.tabs.map(t => t.key)).toEqual(['/dashboard', '/visit/1'])
    expect(st.activeKey).toBe('/visit/1')
  })

  it('falls back to opening the target when the old tab is missing', () => {
    const s = freshR()
    s.getState().replaceTab('/nope', pg('/visit/1'))
    expect(s.getState().tabs.map(t => t.key)).toEqual(['/dashboard', '/visit/1'])
  })
})

describe('tab store — user switch', () => {
  const dashU: TabMeta = { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', pinned: true, closable: false }
  const pg = (k: string): TabMeta => ({ key: k, title: k, kind: 'page', path: k, closable: true })

  it('discards the previous user\'s tabs on a user switch (userId mismatch)', () => {
    localStorage.clear()
    const a = createTabStore({ storageKey: 'u:switch', initialTabs: [dashU], userId: 'user-1' })
    a.getState().openTab(pg('/all'))
    a.getState().openTab(pg('/active'))
    // user-2 logs in against the same storageKey
    const b = createTabStore({ storageKey: 'u:switch', initialTabs: [dashU], userId: 'user-2' })
    expect(b.getState().tabs.map(t => t.key)).toEqual(['/dashboard'])
    expect(b.getState().activeKey).toBe('/dashboard')
  })

  it('restores tabs for the same user', () => {
    localStorage.clear()
    const a = createTabStore({ storageKey: 'u:same', initialTabs: [dashU], userId: 'user-1' })
    a.getState().openTab(pg('/all'))
    const b = createTabStore({ storageKey: 'u:same', initialTabs: [dashU], userId: 'user-1' })
    expect(b.getState().tabs.map(t => t.key)).toEqual(['/dashboard', '/all'])
  })

  it('keeps restoring when userId is not provided (check disabled)', () => {
    localStorage.clear()
    const a = createTabStore({ storageKey: 'u:none', initialTabs: [dashU] })
    a.getState().openTab(pg('/all'))
    const b = createTabStore({ storageKey: 'u:none', initialTabs: [dashU] })
    expect(b.getState().tabs.map(t => t.key)).toEqual(['/dashboard', '/all'])
  })
})

describe('tab store — setTabPath', () => {
  const dashP: TabMeta = { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', pinned: true, closable: false }
  const pg = (k: string): TabMeta => ({ key: k, title: k, kind: 'page', path: k, closable: true })
  it('updates a tab path in place (e.g. query change)', () => {
    localStorage.clear()
    const s = createTabStore({ storageKey: 'tp:test', initialTabs: [dashP] })
    s.getState().openTab(pg('/admin'))
    s.getState().setTabPath('/admin', '/admin?section=role')
    expect(s.getState().tabs.find(t => t.key === '/admin')?.path).toBe('/admin?section=role')
  })
})
