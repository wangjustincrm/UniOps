import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import { BrowserRouter } from 'react-router-dom'
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

function Harness() {
  const api = useTabStoreApi()
  return (
    <>
      <button onClick={() => api.getState().openTab(tabB)}>open-b</button>
      <button onClick={() => api.getState().setActive('/a')}>activate-a</button>
      <TabHost routes={routes} />
    </>
  )
}

describe('TabHost keep-alive', () => {
  it('preserves a tab’s component state across switching away and back', async () => {
    localStorage.clear()
    render(
      <BrowserRouter>
        <TabStoreProvider options={{ storageKey: 'ka:test', initialTabs: [tabA] }}>
          <Harness />
        </TabStoreProvider>
      </BrowserRouter>,
    )

    // Wait for tab A's isolated root to mount and render its initial content.
    await screen.findByText('A count: 0')

    // Increment A's counter to 1.
    fireEvent.click(screen.getByText('A-inc'))
    expect(await screen.findByText('A count: 1')).toBeInTheDocument()

    // Open B (switch away), then back to A.
    fireEvent.click(screen.getByText('open-b'))
    await screen.findByText('B count: 0')
    fireEvent.click(screen.getByText('activate-a'))

    // A's state survived (still 1, not reset to 0) → component was not unmounted.
    expect(await screen.findByText('A count: 1')).toBeInTheDocument()
  })
})
