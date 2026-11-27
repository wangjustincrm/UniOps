import { useEffect } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { TabRouterSync, TabStoreProvider, useTabStoreApi, type RouteDef } from '@uniops/shell'
import { AskHost } from '@/platform-ui'
import { SalesTabBar } from './SalesTabBar'
import { tabSession } from './tabSession'

// The Sales tab bar (stage 4, user 2026-11-27), over the shell's real store: the record-tab cap (the oldest unchanged
// record tab not shown closes, said with Reopen; never a list, an unsaved tab or the shown one), the tab menu, the app's
// own dialog for an unsaved tab, Delete closes and Esc does not, All tabs ▾ with a search and Recently closed.

const routes: RouteDef[] = [
  { path: '/home', element: null, tab: { title: 'Home', keyStrategy: 'static', pinned: true } },
  { path: '/orders', element: null, tab: { title: 'Orders', keyStrategy: 'static' } },
  { path: '/lots', element: null, tab: { title: 'Lots', keyStrategy: 'static' } },
  { path: '/orders/:id', element: null, tab: { title: (p) => `Order ${p.id}`, keyStrategy: 'param', paramName: 'id' } },
]

// What the test drives the app with: its router and the shell's store (set from inside, after each render).
const h: { go: (path: string) => void; store: ReturnType<typeof useTabStoreApi> | null } = { go: () => {}, store: null }
function Driver() {
  const navigate = useNavigate()
  const api = useTabStoreApi()
  useEffect(() => { h.store = api; h.go = (p) => act(() => { void navigate(p) }) })
  return null
}

function app() {
  return render(
    <MemoryRouter initialEntries={['/home']}>
      <AskHost />
      <TabStoreProvider options={{ cap: 500, initialTabs: [{ key: '/home', title: 'Home', kind: 'page', path: '/home', pinned: true, closable: false }] }}>
        <Driver /><TabRouterSync routes={routes} /><SalesTabBar routes={routes} />
      </TabStoreProvider>
    </MemoryRouter>)
}
const titles = () => screen.getAllByRole('tab').map((t) => t.textContent?.replace(/unsaved changes/, ''))
const tab = (name: string) => screen.getByRole('tab', { name: new RegExp(`^${name}`) })

afterEach(() => tabSession.reset())

describe('the record-tab cap', () => {
  it('a new record tab always opens; the oldest unchanged one not shown closes, said with Reopen; lists never', async () => {
    app()
    h.go('/orders'); h.go('/lots')
    for (let i = 1; i <= 13; i++) h.go(`/orders/${i}`)
    expect(titles()).toEqual(['Home', 'Orders', 'Lots', ...Array.from({ length: 12 }, (_, i) => `Order ${i + 2}`)])
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('Order 1 closed to keep 12 open.')
    fireEvent.click(within(status).getByRole('button', { name: 'Reopen' }))
    await waitFor(() => expect(titles()).toContain('Order 1'))
    expect(titles()).not.toContain('Order 2')  // the next oldest made room, said too
    expect(screen.getByRole('status')).toHaveTextContent('Order 2 closed to keep 12 open.')
  })

  it('never an unsaved tab: the next oldest goes; when every other is unsaved, nothing closes and it says so', () => {
    app()
    for (let i = 1; i <= 12; i++) h.go(`/orders/${i}`)
    act(() => h.store!.getState().setDirty('/orders/:id:1', true))
    h.go('/orders/13')
    expect(titles()).toContain('Order 1')
    expect(titles()).not.toContain('Order 2')
    act(() => { for (let i = 3; i <= 13; i++) h.store!.getState().setDirty(`/orders/:id:${i}`, true) })
    h.go('/orders/14')
    expect(screen.getAllByRole('tab')).toHaveLength(14)  // Home + 13 records: none could go
    expect(screen.getByRole('status')).toHaveTextContent('13 record tabs open, 12 with unsaved changes. Save or close some.')
  })

  it('the cap is a setting (All tabs ▾): 6 keeps six', async () => {
    app()
    fireEvent.click(screen.getByRole('button', { name: /^All tabs/ }))
    fireEvent.click(within(await screen.findByRole('group', { name: 'Record tabs kept open' })).getByRole('button', { name: '6' }))
    for (let i = 1; i <= 8; i++) h.go(`/orders/${i}`)
    expect(titles().filter((t) => t?.startsWith('Order '))).toHaveLength(6)
  })
})

describe('closing a tab', () => {
  it('an unsaved tab asks in the app\'s own dialog; kept, or closed and in Recently closed', async () => {
    app()
    h.go('/orders'); h.go('/orders/7')
    act(() => h.store!.getState().setDirty('/orders/:id:7', true))
    fireEvent.click(screen.getByRole('button', { name: 'Close Order 7' }))
    const dialog = await screen.findByRole('dialog', { name: 'Close Order 7?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Keep it open' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(titles()).toContain('Order 7')
    fireEvent.click(screen.getByRole('button', { name: 'Close Order 7' }))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Close anyway' }))
    await waitFor(() => expect(titles()).not.toContain('Order 7'))
    expect(tabSession.get().recent.map((r) => r.path)).toEqual(['/orders/7'])
  })

  it('Delete on a focused tab closes it; Esc does not', () => {
    app()
    h.go('/orders'); h.go('/lots')
    fireEvent.keyDown(tab('Lots'), { key: 'Escape' })
    expect(titles()).toContain('Lots')
    fireEvent.keyDown(tab('Lots'), { key: 'Delete' })
    expect(titles()).not.toContain('Lots')
    fireEvent.keyDown(tab('Home'), { key: 'Delete' })  // pinned: never
    expect(titles()).toContain('Home')
  })
})

describe('a tab\'s menu', () => {
  it('Refresh, Close, Close others (unsaved kept, said), Close to the right, Copy link, Reopen closed tab', async () => {
    const write = vi.fn(async () => {})
    Object.assign(navigator, { clipboard: { writeText: write } })
    app()
    h.go('/orders'); h.go('/lots'); h.go('/orders/1'); h.go('/orders/2')
    act(() => h.store!.getState().setDirty('/lots', true))
    fireEvent.contextMenu(tab('Orders'), { clientX: 5, clientY: 5 })
    const menu = await screen.findByRole('menu')
    expect(within(menu).getAllByRole('menuitem').map((m) => m.textContent?.split('Delete')[0].split('None')[0]))
      .toEqual(['Refresh', 'Close', 'Close others', 'Close to the right', 'Copy link', 'Reopen closed tab'])
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Refresh' }))
    expect(tabSession.get().ticks['/orders']).toBe(1)
    fireEvent.keyDown(tab('Order 1'), { key: 'F10', shiftKey: true })
    fireEvent.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Copy link' }))
    await waitFor(() => expect(write).toHaveBeenCalledWith(`${window.location.origin}/orders/1`))
    fireEvent.contextMenu(tab('Orders'))
    fireEvent.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Close to the right' }))
    expect(titles()).toEqual(['Home', 'Orders', 'Lots'])  // Lots unsaved: kept
    expect(screen.getByRole('status')).toHaveTextContent('1 tab has unsaved changes: kept open.')
    fireEvent.contextMenu(tab('Orders'))
    fireEvent.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: /^Reopen closed tab/ }))
    await waitFor(() => expect(titles()).toContain('Order 2'))  // the last closed
    fireEvent.contextMenu(tab('Orders'))
    fireEvent.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Close others' }))
    expect(titles()).toEqual(['Home', 'Orders', 'Lots'])
  })
})

describe('All tabs ▾', () => {
  it('every tab by kind, with a search; Recently closed reopens', async () => {
    app()
    h.go('/orders'); h.go('/orders/1'); h.go('/orders/2')
    fireEvent.click(screen.getByRole('button', { name: 'Close Order 1' }))
    fireEvent.click(screen.getByRole('button', { name: /^All tabs/ }))
    const all = await screen.findByRole('group', { name: 'All tabs' })
    expect(within(within(all).getByRole('region', { name: 'Lists' })).getAllByRole('button').map((b) => b.textContent)).toEqual(['Home', 'Orders'])
    expect(within(all).getByRole('region', { name: 'Records' })).toHaveTextContent('Order 2')
    fireEvent.change(within(all).getByLabelText('Find a tab'), { target: { value: 'order 1' } })
    expect(within(all).queryByRole('region', { name: 'Lists' })).toBeNull()
    fireEvent.click(within(within(all).getByRole('region', { name: 'Recently closed' })).getByRole('button', { name: /Order 1/ }))
    await waitFor(() => expect(titles()).toContain('Order 1'))
  })
})
