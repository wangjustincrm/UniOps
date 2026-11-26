import { act, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { TabStoreProvider, useTabStoreApi, type RouteDef, type TabStoreState } from '@uniops/shell'
import { usePageActive } from '@/platform-ui'
import { TabActivity } from './TabActivity'

// A page learns whether its app tab is the shown one from the shell's store, with no change to the shell (user 2026-11-26).

const ROUTES: RouteDef[] = [
  { path: '/orders', element: null, tab: { title: 'Orders', keyStrategy: 'static' } },
  { path: '/invoices', element: null, tab: { title: 'Invoices', keyStrategy: 'static' } },
]
const Says = () => <p>{usePageActive() ? 'active' : 'kept alive'}</p>
const held: { store: { getState: () => TabStoreState } | null } = { store: null }
function Grab() {
  const api = useTabStoreApi()
  useEffect(() => { held.store = api }, [api])
  return null
}

describe('TabActivity', () => {
  it('active only while its tab is the shown one', () => {
    render(<TabStoreProvider options={{ initialTabs: [{ key: '/orders', title: 'Orders', kind: 'page', path: '/orders', closable: true },
      { key: '/invoices', title: 'Invoices', kind: 'page', path: '/invoices', closable: true }] }}>
      <Grab />
      <MemoryRouter initialEntries={['/orders']}><TabActivity routes={ROUTES}><Says /></TabActivity></MemoryRouter>
    </TabStoreProvider>)
    act(() => held.store!.getState().setActive('/orders'))
    expect(screen.getByText('active')).toBeInTheDocument()
    act(() => held.store!.getState().setActive('/invoices'))
    expect(screen.getByText('kept alive')).toBeInTheDocument()
    act(() => held.store!.getState().setActive('/orders'))
    expect(screen.getByText('active')).toBeInTheDocument()
  })

  it('outside a tab store (an embedded page): always active', () => {
    render(<MemoryRouter initialEntries={['/orders']}><TabActivity routes={ROUTES}><Says /></TabActivity></MemoryRouter>)
    expect(screen.getByText('active')).toBeInTheDocument()
  })
})
