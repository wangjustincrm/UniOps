import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { useTabTitle } from './useTabTitle'
import { useTabDirty } from './useTabDirty'
import { useReplaceTab } from './useReplaceTab'
import { TabBackLink } from './TabBackLink'
import type { RouteDef } from './types'

/**
 * EPMS and OA render their pages WITHOUT a TabStoreProvider when they are
 * embedded in an iframe (Finance's AP list drills into an invoice this way, and
 * the embedded app deliberately hides its own tab bar). A page that happens to
 * use a tab hook must still render there — being outside a tab shell is a
 * supported mode, not a programming error.
 */
function InvoiceDetail() {
  useTabTitle(routes, 'IVN#2026000062')
  useTabDirty(routes, false)
  const replaceTab = useReplaceTab(routes)
  return (
    <div>
      <span>invoice body</span>
      <button onClick={() => replaceTab('/invoices')}>submit</button>
      <TabBackLink routes={routes} to="/invoices">Back to Invoices</TabBackLink>
    </div>
  )
}

const routes: RouteDef[] = [
  { path: '/invoices', element: <div>invoice list</div>, tab: { title: 'Invoices', keyStrategy: 'static' } },
  {
    path: '/invoices/:id',
    element: <InvoiceDetail />,
    tab: { title: (p) => `Invoice ${p.id}`, keyStrategy: 'param', paramName: 'id' },
  },
]

/** Mirrors the embedded branch of EPMS/OA AppLayout: plain Routes, no provider. */
function EmbeddedApp() {
  return (
    <BrowserRouter>
      <Routes>
        {routes.map((r) => (
          <Route key={r.path} path={r.path} element={r.element} />
        ))}
      </Routes>
    </BrowserRouter>
  )
}

describe('tab hooks outside a TabStoreProvider (iframe embed)', () => {
  it('renders a page that uses the tab hooks, instead of throwing', () => {
    window.history.pushState({}, '', '/invoices/abc')
    render(<EmbeddedApp />)
    expect(screen.getByText('invoice body')).toBeInTheDocument()
  })

  it('a back link still navigates when there is no tab store to close', () => {
    window.history.pushState({}, '', '/invoices/abc')
    render(<EmbeddedApp />)

    fireEvent.click(screen.getByText('Back to Invoices'))

    expect(screen.getByText('invoice list')).toBeInTheDocument()
  })

  it('a replace-tab navigation still navigates without a tab store', () => {
    window.history.pushState({}, '', '/invoices/abc')
    render(<EmbeddedApp />)

    fireEvent.click(screen.getByText('submit'))

    expect(screen.getByText('invoice list')).toBeInTheDocument()
  })
})
