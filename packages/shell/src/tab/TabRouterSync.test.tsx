import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import { BrowserRouter, Link, useSearchParams } from 'react-router-dom'
import { TabStoreProvider } from './TabStoreContext'
import { TabHost } from './TabHost'
import { TabRouterSync } from './TabRouterSync'
import type { RouteDef } from './types'

// A create page that, like New PO / New GR, reads its query ONCE, at mount.
function CreateForm({ label }: { label: string }) {
  const [params] = useSearchParams()
  const [from] = useState(params.get('from') ?? 'blank')
  return <p>{label} form from {from}</p>
}

const routes = (reloadOnQuery: boolean): RouteDef[] => [
  { path: '/list', element: <p>List page</p>, tab: { title: 'List', keyStrategy: 'static' } },
  { path: '/new', element: <CreateForm label="New" />, tab: { title: 'New', keyStrategy: 'static', reloadOnQuery } },
]

function mount(reloadOnQuery: boolean) {
  window.history.pushState({}, '', '/list')
  const defs = routes(reloadOnQuery)
  render(
    <BrowserRouter>
      <TabStoreProvider options={{ initialTabs: [] }}>
        <Link to="/new">new-blank</Link>
        <Link to="/new?from=A">create-from-A</Link>
        <Link to="/new?from=B">create-from-B</Link>
        <Link to="/list">to-list</Link>
        <TabRouterSync routes={defs} />
        <TabHost routes={defs} />
      </TabStoreProvider>
    </BrowserRouter>,
  )
}

const visible = (text: string) => {
  const el = screen.queryByText(text)
  return !!el && !el.closest('[hidden]')
}

describe('TabRouterSync — reloadOnQuery create tabs (ISS-0150)', () => {
  it('a link with another query re-opens the open create tab from that query', async () => {
    mount(true)
    fireEvent.click(screen.getByText('new-blank'))
    expect(await screen.findByText('New form from blank')).toBeInTheDocument()

    // Away to another tab, then "Create PO" from document A: not the old blank form.
    fireEvent.click(screen.getByText('to-list'))
    fireEvent.click(screen.getByText('create-from-A'))
    expect(await screen.findByText('New form from A')).toBeInTheDocument()
    expect(visible('New form from A')).toBe(true)

    // Straight from A's form to B's: never A's data under B's URL.
    fireEvent.click(screen.getByText('create-from-B'))
    expect(await screen.findByText('New form from B')).toBeInTheDocument()
    expect(screen.queryByText('New form from A')).toBeNull()
  })

  it('still keeps one tab for the route', async () => {
    mount(true)
    fireEvent.click(screen.getByText('create-from-A'))
    fireEvent.click(screen.getByText('to-list'))
    fireEvent.click(screen.getByText('create-from-B'))
    expect(await screen.findByText('New form from B')).toBeInTheDocument()
    expect(screen.getAllByText(/New form from/)).toHaveLength(1)
  })

  it('without the flag the open tab is only focused (unchanged behaviour)', async () => {
    mount(false)
    fireEvent.click(screen.getByText('new-blank'))
    expect(await screen.findByText('New form from blank')).toBeInTheDocument()
    fireEvent.click(screen.getByText('to-list'))
    fireEvent.click(screen.getByText('create-from-A'))
    expect(await screen.findByText('New form from blank')).toBeInTheDocument()
    expect(visible('New form from blank')).toBe(true)
  })
})
