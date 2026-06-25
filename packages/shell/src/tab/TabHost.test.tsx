import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { createContext, useContext, useState } from 'react'
import { BrowserRouter, useParams } from 'react-router-dom'
import { TabStoreProvider, useTabStoreApi } from './TabStoreContext'
import { TabHost } from './TabHost'
import type { RouteDef, TabMeta } from './types'

function Counter({ label }: { label: string }) {
  const [n, setN] = useState(0)
  return (
    <div>
      <span>{label} count: {n}</span>
      <button onClick={() => setN((v) => v + 1)}>{label}-inc</button>
    </div>
  )
}

const routes: RouteDef[] = [
  { path: '/a', element: <Counter label="A" />, tab: { title: 'A', keyStrategy: 'static' } },
  { path: '/b', element: <Counter label="B" />, tab: { title: 'B', keyStrategy: 'static' } },
]

const tabA: TabMeta = { key: '/a', title: 'A', kind: 'page', path: '/a', closable: true }
const tabB: TabMeta = { key: '/b', title: 'B', kind: 'page', path: '/b', closable: true }

function Harness({ routeDefs }: { routeDefs: RouteDef[] }) {
  const api = useTabStoreApi()
  return (
    <>
      <button onClick={() => api.getState().openTab(tabB)}>open-b</button>
      <button onClick={() => api.getState().setActive('/a')}>activate-a</button>
      <TabHost routes={routeDefs} />
    </>
  )
}

describe('TabHost keep-alive', () => {
  it('preserves a tab’s component state across switching away and back', () => {
    localStorage.clear()
    render(
      <BrowserRouter>
        <TabStoreProvider options={{ storageKey: 'ka:test', initialTabs: [tabA] }}>
          <Harness routeDefs={routes} />
        </TabStoreProvider>
      </BrowserRouter>,
    )

    expect(screen.getByText('A count: 0')).toBeInTheDocument()

    // Increment A's counter to 1.
    fireEvent.click(screen.getByText('A-inc'))
    expect(screen.getByText('A count: 1')).toBeInTheDocument()

    // Open B (switch away), then back to A.
    fireEvent.click(screen.getByText('open-b'))
    expect(screen.getByText('B count: 0')).toBeInTheDocument()
    fireEvent.click(screen.getByText('activate-a'))

    // A's state survived (still 1, not reset to 0) → component was not unmounted.
    expect(screen.getByText('A count: 1')).toBeInTheDocument()
  })
})

describe('TabHost shared context', () => {
  const Ctx = createContext('default')

  function CtxProbe() {
    const value = useContext(Ctx)
    return <div>ctx value: {value}</div>
  }

  const ctxRoutes: RouteDef[] = [
    { path: '/ctx', element: <CtxProbe />, tab: { title: 'Ctx', keyStrategy: 'static' } },
  ]
  const tabCtx: TabMeta = { key: '/ctx', title: 'Ctx', kind: 'page', path: '/ctx', closable: true }

  it('shares the app-level React context with kept-alive tabs (no separate React root)', () => {
    localStorage.clear()
    render(
      <Ctx.Provider value="from-app">
        <BrowserRouter>
          <TabStoreProvider options={{ storageKey: 'ka:ctx-test', initialTabs: [tabCtx] }}>
            <TabHost routes={ctxRoutes} />
          </TabStoreProvider>
        </BrowserRouter>
      </Ctx.Provider>,
    )

    expect(screen.getByText('ctx value: from-app')).toBeInTheDocument()
  })
})

describe('TabHost params', () => {
  function VisitProbe() {
    const { visitId } = useParams()
    return <div>visit id: {visitId}</div>
  }

  const paramRoutes: RouteDef[] = [
    {
      path: '/visit/:visitId',
      element: <VisitProbe />,
      tab: { title: 'V', keyStrategy: 'param', paramName: 'visitId' },
    },
  ]
  const tabVisit: TabMeta = {
    key: '/visit/:visitId:42',
    title: 'V',
    kind: 'page',
    path: '/visit/42',
    closable: true,
  }

  it('provides correct route params to a kept-alive page via Routes location', () => {
    localStorage.clear()
    render(
      <BrowserRouter>
        <TabStoreProvider options={{ storageKey: 'ka:param-test', initialTabs: [tabVisit] }}>
          <TabHost routes={paramRoutes} />
        </TabStoreProvider>
      </BrowserRouter>,
    )

    expect(screen.getByText('visit id: 42')).toBeInTheDocument()
  })
})
