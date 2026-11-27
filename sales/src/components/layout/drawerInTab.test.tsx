import { useEffect, useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { TabHost, TabRouterSync, TabStoreProvider, useTabStoreApi, type RouteDef } from '@uniops/shell'
import { FormSheet } from '@/platform-ui'
import { SalesTabBar } from './SalesTabBar'
import { withTabActivity } from './tabRoutes'
import { tabSession } from './tabSession'

// "A drawer belongs to its tab" (user 2026-11-27, option 1): a sheet opened in a tab's page is drawn in that tab's layer —
// under the tab bar, which stays usable — and hides with its tab: its input, and the focus inside it, are as they were
// when the tab is shown again; a dot on the tab says a record is open there. Esc in another tab leaves it alone.

function OrdersPage() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <h1>Orders</h1>
      <button type="button" onClick={() => setOpen(true)}>Open SHP-9649</button>
      <FormSheet open={open} onOpenChange={setOpen} title="SHP-9649"><label>Note <input aria-label="Note" /></label></FormSheet>
    </>
  )
}
const routes: RouteDef[] = [
  { path: '/orders', element: <OrdersPage />, tab: { title: 'Orders', keyStrategy: 'static' } },
  { path: '/items/:code', element: <h1>Item</h1>, tab: { title: (p) => p.code, keyStrategy: 'param', paramName: 'code' } },
]
const TAB_ROUTES = withTabActivity(routes)

const h: { go: (p: string) => void; store: ReturnType<typeof useTabStoreApi> | null } = { go: () => {}, store: null }
function Driver() {
  const navigate = useNavigate()
  const api = useTabStoreApi()
  useEffect(() => { h.store = api; h.go = (p) => act(() => { void navigate(p) }) })
  return null
}

function app() {
  return render(
    <MemoryRouter initialEntries={['/orders']}>
      <TabStoreProvider options={{ cap: 500, initialTabs: [] }}>
        <Driver /><TabRouterSync routes={routes} />
        <SalesTabBar routes={routes} />
        <main style={{ position: 'relative', height: 600 }}><TabHost routes={TAB_ROUTES} /></main>
      </TabStoreProvider>
    </MemoryRouter>)
}
afterEach(() => tabSession.reset())

describe('a drawer belongs to its tab', () => {
  it('drawn in its tab (not over the window): the tab bar stays usable; it hides with the tab and comes back as it was', async () => {
    app()
    fireEvent.click(await screen.findByRole('button', { name: 'Open SHP-9649' }))
    const sheet = await screen.findByRole('dialog', { name: 'SHP-9649' })
    const layer = sheet.closest('[data-sheet-layer]')!
    expect(layer).not.toBeNull()
    expect(layer.closest('main')).not.toBeNull()  // inside the tab's content, under the tab bar
    expect(screen.getByRole('tablist', { name: 'Open tabs' })).not.toHaveAttribute('aria-hidden')
    fireEvent.change(within(sheet).getByLabelText('Note'), { target: { value: 'checked the lines' } })
    within(sheet).getByLabelText('Note').focus()
    h.go('/items/CF0093')  // a reference from the sheet
    await waitFor(() => expect(sheet.closest('[hidden]')).not.toBeNull())  // hidden with its tab, still there
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Item' })).toHaveFocus())  // the new tab's page has it
    expect(screen.getByRole('tab', { name: 'Orders, a record is open' })).toBeInTheDocument()
    fireEvent.keyDown(document.body, { key: 'Escape' })  // Esc in the item's tab: the hidden sheet stays open
    fireEvent.click(screen.getByRole('tab', { name: 'Orders, a record is open' }))
    await waitFor(() => expect(sheet.closest('[hidden]')).toBeNull())
    expect(screen.getByRole('dialog', { name: 'SHP-9649' })).toBe(sheet)
    expect(within(sheet).getByLabelText('Note')).toHaveValue('checked the lines')  // unsaved input kept
    await waitFor(() => expect(within(sheet).getByLabelText('Note')).toHaveFocus())  // the focus back where it was
    expect(screen.getByRole('tab', { name: /^Orders/ })).not.toHaveAccessibleName('Orders, a record is open')  // shown: no dot
  })

  it('a tab shown for the first time takes the focus on its page; Esc in its own tab still closes the sheet', async () => {
    app()
    fireEvent.click(await screen.findByRole('button', { name: 'Open SHP-9649' }))
    const sheet = await screen.findByRole('dialog', { name: 'SHP-9649' })
    within(sheet).getByLabelText('Note').focus()
    h.go('/items/CF0093')
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Item' })).toHaveFocus())
    fireEvent.click(screen.getByRole('tab', { name: /^Orders/ }))
    await waitFor(() => expect(sheet.closest('[hidden]')).toBeNull())
    fireEvent.keyDown(within(sheet).getByLabelText('Note'), { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'SHP-9649' })).toBeNull())
    expect(tabSession.get().sheets['/orders']).toBe(false)
  })

  it('two quick navigations: the last one wins, the store follows the route', () => {
    app()
    act(() => { h.go('/items/A'); h.go('/items/B') })
    const s = h.store!.getState()
    expect(s.tabs.find((t) => t.key === s.activeKey)?.path).toBe('/items/B')
  })
})
