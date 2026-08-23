import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import { TabStoreProvider, useTabStore, useTabStoreApi } from './TabStoreContext'
import type { TabMeta } from './types'

const dash: TabMeta = { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', pinned: true, closable: false }
const extra: TabMeta = { key: '/po', title: 'Purchase Orders', kind: 'page', path: '/po', closable: true }

function Inner() {
  const tabs = useTabStore((s) => s.tabs)
  const api = useTabStoreApi()
  return (
    <>
      <span data-testid="keys">{tabs.map((t) => t.key).join('|')}</span>
      <button onClick={() => api.getState().openTab(extra)}>open</button>
    </>
  )
}

/** Lets a test drive `userId` the way an auth store would. */
function Harness({ initialUserId }: { initialUserId: string | undefined }) {
  const [userId, setUserId] = useState(initialUserId)
  return (
    <>
      <button onClick={() => setUserId('user-2')}>to-user-2</button>
      <button onClick={() => setUserId(undefined)}>to-unknown</button>
      <TabStoreProvider options={{ initialTabs: [dash], userId }}>
        <Inner />
      </TabStoreProvider>
    </>
  )
}

describe('TabStoreProvider — store identity', () => {
  it('discards the previous user’s tabs when the user changes', () => {
    render(<Harness initialUserId="user-1" />)
    fireEvent.click(screen.getByText('open'))
    expect(screen.getByTestId('keys').textContent).toBe('/dashboard|/po')

    fireEvent.click(screen.getByText('to-user-2'))

    expect(screen.getByTestId('keys').textContent).toBe('/dashboard')
  })

  it('keeps tabs when userId momentarily goes undefined (auth rehydrate, not a switch)', () => {
    render(<Harness initialUserId="user-1" />)
    fireEvent.click(screen.getByText('open'))

    fireEvent.click(screen.getByText('to-unknown'))

    expect(screen.getByTestId('keys').textContent).toBe('/dashboard|/po')
  })

  it('keeps tabs when the user id merely resolves for the first time', () => {
    render(<Harness initialUserId={undefined} />)
    fireEvent.click(screen.getByText('open'))

    fireEvent.click(screen.getByText('to-user-2'))

    expect(screen.getByTestId('keys').textContent).toBe('/dashboard|/po')
  })
})
