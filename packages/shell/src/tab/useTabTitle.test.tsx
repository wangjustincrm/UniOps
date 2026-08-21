import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import { BrowserRouter } from 'react-router-dom'
import { TabStoreProvider, useTabStore } from './TabStoreContext'
import { TabHost } from './TabHost'
import { useTabTitle } from './useTabTitle'
import type { RouteDef, TabMeta } from './types'

const short = (id: string) => (id.length > 8 ? id.slice(0, 8) : id)

/** Stands in for a detail page: renders before its document number is known. */
function PoDetail() {
  // `undefined` until "loaded", exactly like a react-query result.
  const [poNumber, setPoNumber] = useState<string | undefined>(undefined)
  useTabTitle(routes, poNumber && `PO ${poNumber}`)
  return <button onClick={() => setPoNumber('PO-20260815-0007')}>load</button>
}

const routes: RouteDef[] = [
  {
    path: '/po/:id',
    element: <PoDetail />,
    tab: { title: (p) => `PO ${short(p.id)}`, keyStrategy: 'param', paramName: 'id' },
  },
]

const poTab: TabMeta = {
  key: '/po/:id:a5f40064-1111-2222-3333-444455556666',
  title: 'PO a5f40064',
  kind: 'page',
  path: '/po/a5f40064-1111-2222-3333-444455556666',
  closable: true,
}

function Titles() {
  const tabs = useTabStore((s) => s.tabs)
  return <span data-testid="titles">{tabs.map((t) => t.title).join('|')}</span>
}

describe('useTabTitle', () => {
  it('renames the tab to the document number once the page has loaded it', () => {
    render(
      <BrowserRouter>
        <TabStoreProvider options={{ initialTabs: [poTab] }}>
          <Titles />
          <TabHost routes={routes} />
        </TabStoreProvider>
      </BrowserRouter>,
    )

    // Before load: the route table's UUID-derived fallback.
    expect(screen.getByTestId('titles')).toHaveTextContent('PO a5f40064')

    fireEvent.click(screen.getByText('load'))

    expect(screen.getByTestId('titles')).toHaveTextContent('PO PO-20260815-0007')
  })
})
