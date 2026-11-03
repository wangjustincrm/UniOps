import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ListPage } from './patterns/ListPage'
import { PermissionProvider } from './patterns/PermissionGate'
import { RowActions } from './patterns/RowActions'
import type { ListMeta } from './resource/listMeta'

// RowActions: compact icon buttons per row — shown with their permission,
// spinning and blocked while they run, asking first when they must, and never
// opening the row.

const meta: ListMeta = { version: 1, searchable: false, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }

function page(keys: string[], onAction: () => Promise<unknown>, confirm?: () => { title: string } | null) {
  const resource = { basePath: '/things', meta: async () => meta, list: vi.fn(async () => ({ items: [{ id: 1, name: 'first' }], total: 1 })) }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><PermissionProvider can={(k) => keys.includes(k)}>
    <ListPage<{ id: number; name: string }> resource={resource} title="Things" columns={[{ accessorKey: 'name', header: 'Name' }]}
      detailTitle={(r) => `Thing ${r.name}`} renderDetail={(r) => <p>detail of {r.name}</p>}
      rowActions={() => <RowActions actions={[
        { key: 'go', label: 'Go now', icon: <span>⟳</span>, permission: 'x.run', onAction, confirm },
        { key: 'gone', label: 'Hidden one', icon: <span>×</span>, hidden: true, onAction },
      ]} />} />
  </PermissionProvider></QueryClientProvider>)
}

describe('RowActions', () => {
  it('shows an action only with its permission (and never a hidden one)', async () => {
    const { unmount } = page([], async () => {})
    await screen.findByText('first')
    expect(screen.queryByRole('button', { name: 'Go now' })).toBeNull()
    unmount()
    page(['x.run'], async () => {})
    expect(await screen.findByRole('button', { name: 'Go now' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Hidden one' })).toBeNull()
  })

  it('a click runs it without opening the row; it spins and is blocked until it ends', async () => {
    let finish: () => void = () => {}
    const onAction = vi.fn(() => new Promise<void>((r) => { finish = r }))
    page(['x.run'], onAction)
    const go = await screen.findByRole('button', { name: 'Go now' })
    fireEvent.click(go)
    expect(onAction).toHaveBeenCalledOnce()
    expect(screen.queryByRole('dialog')).toBeNull()  // the row's sheet did not open
    await waitFor(() => expect(go).toBeDisabled())
    expect(go).toHaveAttribute('aria-busy', 'true')
    fireEvent.click(go)
    expect(onAction).toHaveBeenCalledOnce()  // blocked while it runs
    await act(async () => { finish() })
    await waitFor(() => expect(go).toBeEnabled())
  })

  it('asks first when its confirm says so at the click', async () => {
    const onAction = vi.fn(async () => {})
    page(['x.run'], onAction, () => ({ title: 'Sure?' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Go now' }))
    const ask = await screen.findByRole('dialog', { name: 'Sure?' })
    expect(onAction).not.toHaveBeenCalled()
    fireEvent.click(within(ask).getByRole('button', { name: 'Go now' }))
    await waitFor(() => expect(onAction).toHaveBeenCalledOnce())
  })
})
