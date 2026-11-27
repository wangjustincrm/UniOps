import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, useParams } from 'react-router-dom'
import { TabHost, TabRouterSync, TabStoreProvider, useTabStore, type RouteDef } from '@uniops/shell'
import { PermissionProvider, RecordSheet, RefLink } from '@/platform-ui'
import { moduleRoutes } from '@/app/routes'
import { forgetClosed } from './backLinks'
import { ModuleRoutesContext } from './moduleRoutesContext'
import { RecordNavProvider } from './RecordNavProvider'
import { RecordRoute } from './RecordRoute'
import { withTabActivity } from '@/components/layout/tabRoutes'

// Records in tabs, as EPMS does (user 2026-11-27): a reference opens the record's own tab, one per record (a second
// click focuses it); its "← Back to …" names the tab it came from, closes the record tab and returns there; reached
// from nowhere (a typed address, a link from outside), Back goes to the record's list.

function Orders() {
  return <p>Order SHP-9649: <RefLink record={{ type: 'item', key: 'CF0093' }}>CF0093</RefLink></p>
}
function OrdersWithSheet() {
  return (
    <>
      <RecordSheet title="SHP-9650" record={{ type: 'order', key: 50 }} open onOpenChange={() => {}}
        tabs={[{ id: 'l', label: 'Lines', render: () => <RefLink record={{ type: 'item', key: 'CF0092' }}>CF0092</RefLink> }]} />
    </>
  )
}
function Item() {
  const code = useParams().code ?? ''
  return <RecordRoute type="item" title={code}>{() => <RecordSheet title={code} record={{ type: 'item', key: code }} tabs={[]} />}</RecordRoute>
}
const routes: RouteDef[] = [
  { path: '/sheet', element: <OrdersWithSheet />, tab: { title: 'Orders', keyStrategy: 'static' } },
  { path: '/orders', element: <Orders />, tab: { title: 'Orders', keyStrategy: 'static' } },
  { path: '/config/items', element: <p>Items list</p>, tab: { title: 'Items', keyStrategy: 'static' } },
  { path: '/items/:code', element: <Item />, tab: { title: (p) => p.code, keyStrategy: 'param', paramName: 'code' } },
]

function Tabs() {
  const tabs = useTabStore((s) => s.tabs)
  const active = useTabStore((s) => s.activeKey)
  return <ul aria-label="tabs">{tabs.map((t) => <li key={t.key} aria-current={t.key === active || undefined}>{t.title}</li>)}</ul>
}

function app(at: string) {
  const qc = new QueryClient()
  return render(
    <QueryClientProvider client={qc}><PermissionProvider can={() => true}>
      <MemoryRouter initialEntries={[at]}>
        <TabStoreProvider options={{ initialTabs: [] }}>
          <ModuleRoutesContext.Provider value={routes}><RecordNavProvider routes={routes}>
            <TabRouterSync routes={routes} />
            <Tabs />
            <main style={{ position: 'relative', height: 400 }}><TabHost routes={TAB_ROUTES} /></main>
          </RecordNavProvider></ModuleRoutesContext.Provider>
        </TabStoreProvider>
      </MemoryRouter>
    </PermissionProvider></QueryClientProvider>)
}

const TAB_ROUTES = withTabActivity(routes)
const tabTitles = () => within(screen.getByRole('list', { name: 'tabs' })).getAllByRole('listitem').map((li) => li.textContent)
const activeTab = () => within(screen.getByRole('list', { name: 'tabs' })).getAllByRole('listitem').find((li) => li.getAttribute('aria-current'))?.textContent

describe('records in their own tabs', () => {
  afterEach(() => forgetClosed(() => false))  // the session's memory of where tabs came from: one per test

  it('a reference opens the record\'s tab, a second click focuses it; Back closes it and returns to where it came from', async () => {
    app('/orders')
    fireEvent.click(await screen.findByRole('link', { name: 'CF0093' }))
    await waitFor(() => expect(tabTitles()).toEqual(['Orders', 'CF0093']))
    expect(activeTab()).toBe('CF0093')
    const page = await screen.findByRole('region', { name: 'CF0093' })
    const back = within(page).getByRole('link', { name: '← Back to Orders' })
    expect(back).toHaveAttribute('href', '/orders')
    expect(within(page).queryByRole('button', { name: 'Open in its tab' })).toBeNull()
    fireEvent.click(back)
    await waitFor(() => expect(tabTitles()).toEqual(['Orders']))
    expect(activeTab()).toBe('Orders')
    fireEvent.click(screen.getByRole('link', { name: 'CF0093' }))
    await waitFor(() => expect(tabTitles()).toEqual(['Orders', 'CF0093']))
    fireEvent.click(screen.getByRole('link', { name: 'CF0093', hidden: true }))  // again, from the Orders tab (now behind)
    await waitFor(() => expect(activeTab()).toBe('CF0093'))
    expect(tabTitles()).toEqual(['Orders', 'CF0093'])  // one tab per record: focused, not a second
  })

  it('from a record\'s sheet: Back names that record, and goes back to its tab, where the sheet is', async () => {
    app('/sheet')
    const sheet = await screen.findByRole('dialog', { name: 'SHP-9650' })
    fireEvent.click(within(sheet).getByRole('link', { name: 'CF0092' }))
    await waitFor(() => expect(activeTab()).toBe('CF0092'))
    const back = await screen.findByRole('link', { name: '← Back to Order SHP-9650' })
    expect(back).toHaveAttribute('href', '/sheet')
  })

  it('reached from nowhere (a typed address): Back goes to the record\'s list', async () => {
    app('/items/CF0093')
    const page = await screen.findByRole('region', { name: 'CF0093' })
    expect(within(page).getByRole('link', { name: '← Back to Items' })).toHaveAttribute('href', '/config/items')
  })
})

describe('the module\'s record routes', () => {
  it('one tab per record for an order, an item and a lot (keyStrategy param)', () => {
    const byPath = new Map(moduleRoutes.map((r) => [r.path, r.tab]))
    expect(byPath.get('/orders/:id')).toMatchObject({ keyStrategy: 'param', paramName: 'id' })
    expect(byPath.get('/items/:code')).toMatchObject({ keyStrategy: 'param', paramName: 'code' })
    expect(byPath.get('/lots/:lot')).toMatchObject({ keyStrategy: 'param', paramName: 'lot' })
  })
})
