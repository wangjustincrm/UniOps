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
