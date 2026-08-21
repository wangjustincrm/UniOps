import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { TabStoreProvider, useTabStore, useTabStoreApi } from './TabStoreContext'
import { TabHost } from './TabHost'
import { TabBackLink } from './TabBackLink'
import type { RouteDef, TabMeta } from './types'

function PoList() {
  return <div>po list</div>
}

function PoDetail() {
  return (
    <TabBackLink routes={routes} to="/po">
      Back to PO List
    </TabBackLink>
  )
}

const routes: RouteDef[] = [
  { path: '/po', element: <PoList />, tab: { title: 'Purchase Orders', keyStrategy: 'static' } },
  {
    path: '/po/:id',
    element: <PoDetail />,
    tab: { title: (p) => `PO ${p.id}`, keyStrategy: 'param', paramName: 'id' },
  },
]

const listTab: TabMeta = { key: '/po', title: 'Purchase Orders', kind: 'page', path: '/po', closable: true }
const detailTab: TabMeta = { key: '/po/:id:abc', title: 'PO abc', kind: 'page', path: '/po/abc', closable: true }

function Keys() {
  const tabs = useTabStore((s) => s.tabs)
  const activeKey = useTabStore((s) => s.activeKey)
  return (
    <>
      <span data-testid="keys">{tabs.map((t) => t.key).join('|')}</span>
      <span data-testid="active">{activeKey}</span>
    </>
  )
}

function Opener({ tab }: { tab: TabMeta }) {
  const api = useTabStoreApi()
  return <button onClick={() => api.getState().openTab(tab)}>open</button>
}

function renderWith(initialTabs: TabMeta[]) {
  return render(
    <BrowserRouter>
      <TabStoreProvider options={{ initialTabs }}>
        <Keys />
        <Opener tab={detailTab} />
        <TabHost routes={routes} />
      </TabStoreProvider>
    </BrowserRouter>,
  )
}

describe('TabBackLink / useBackTab', () => {
  it('closes the current tab and focuses the destination when it is already open', () => {
    renderWith([listTab])
    fireEvent.click(screen.getByText('open')) // open the PO detail tab from the list
    expect(screen.getByTestId('keys')).toHaveTextContent('/po|/po/:id:abc')

    fireEvent.click(screen.getByText('Back to PO List'))

    expect(screen.getByTestId('keys')).toHaveTextContent('/po')
    expect(screen.getByTestId('keys').textContent).toBe('/po')
    expect(screen.getByTestId('active')).toHaveTextContent('/po')
  })

  it('turns the current tab into the destination when the destination is not open', () => {
    renderWith([detailTab])

    fireEvent.click(screen.getByText('Back to PO List'))

    expect(screen.getByTestId('keys').textContent).toBe('/po')
    expect(screen.getByTestId('active')).toHaveTextContent('/po')
  })

  it('leaves a pinned tab alone and just opens the destination', () => {
    renderWith([{ ...detailTab, pinned: true, closable: false }])

    fireEvent.click(screen.getByText('Back to PO List'))

    expect(screen.getByTestId('keys').textContent).toBe('/po/:id:abc|/po')
    expect(screen.getByTestId('active')).toHaveTextContent('/po')
  })

  it('does not intercept modified clicks (ctrl/cmd-click opens a browser tab)', () => {
    renderWith([listTab])
    fireEvent.click(screen.getByText('open'))

    const link = screen.getByText('Back to PO List')
    const evt = new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true })
    link.dispatchEvent(evt)

    expect(evt.defaultPrevented).toBe(false)
    expect(screen.getByTestId('keys').textContent).toBe('/po|/po/:id:abc')
  })
})
