import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ListPage } from './patterns/ListPage'
import { PermissionProvider } from './patterns/PermissionGate'
import { defineActions, resultText } from './patterns/recordActions'
import type { ActionOut, ActionRunBody } from './resource/ResourceClient'
import type { ListMeta } from './resource/listMeta'

// Record actions (user 2026-11-23): defined once; the row "⋯" menu, the selection bar and the confirm step with the
// server's dry run. Checkboxes only for someone holding a group action.

type Thing = { id: number; version: number; name: string; done: boolean }
const meta: ListMeta = { version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }
const ROWS: Thing[] = [{ id: 1, version: 4, name: 'first', done: false }, { id: 2, version: 1, name: 'second', done: true },
  { id: 3, version: 2, name: 'third', done: false }]
const ACTIONS = defineActions<Thing>([{
  id: 'finish', label: 'Finish', permission: 'x.finish', scope: 'group', placement: ['row', 'bulk'],
  eligible: (t) => (t.done ? 'Already finished' : true), input: { reason: { label: 'Why' } },
  will: (n) => `${n} will be finished`, done: (n) => `${n} finished`,
}])

function page(keys: string[], action = vi.fn(async (_id: string, body: ActionRunBody): Promise<ActionOut> => {
  const ids = Object.keys(body.ids ?? {}).map(Number)
  const results = ids.map((id) => (id === 2 ? { id, status: 'skipped' as const, reason: 'already finished' } : { id, status: 'done' as const }))
  return { action: 'finish', dry_run: !!body.dry_run, results,
    counts: { done: results.filter((r) => r.status === 'done').length, skipped: results.filter((r) => r.status === 'skipped').length, failed: 0 } }
})) {
  const resource = { basePath: '/things', meta: async () => meta, list: vi.fn(async () => ({ items: ROWS, total: 3 })), action }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><PermissionProvider can={(k) => keys.includes(k)}>
    <ListPage<Thing> resource={resource} title="Things" columns={[{ accessorKey: 'name', header: 'Name' }]} actions={ACTIONS}
      recordName={(t) => t.name} />
  </PermissionProvider></QueryClientProvider>)
  return action
}

describe('record actions on a list', () => {
  it('a reader sees the list as before: no checkboxes, no ⋯ menu', async () => {
    page(['x.read'])
    await screen.findByText('first')
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.queryByRole('button', { name: /Actions for/ })).toBeNull()
  })

  it('selected rows show the bar with how many each action fits; Confirm runs the dry run, then the action', async () => {
    const action = page(['x.finish'])
    await screen.findByText('first')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all on this page' }))
    const bar = screen.getByRole('toolbar', { name: 'Selected records' })
    expect(within(bar).getByText('3 selected')).toBeInTheDocument()
    fireEvent.click(within(bar).getByRole('button', { name: 'Finish (2 of 3)' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('2 will be finished')).toBeInTheDocument()
    expect(within(dialog).getByText('second: already finished')).toBeInTheDocument()
    expect(action).toHaveBeenCalledWith('finish', { ids: { 1: 4, 2: 1, 3: 2 }, dry_run: true })
    fireEvent.change(within(dialog).getByLabelText(/Why/), { target: { value: 'all done today' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finish (2)' }))
    await waitFor(() => expect(action).toHaveBeenLastCalledWith('finish', { ids: { 1: 4, 2: 1, 3: 2 }, input: { reason: 'all done today' } }))
    expect(await screen.findByText('2 finished · 1 skipped')).toBeInTheDocument()  // the list's status row
    await waitFor(() => expect(screen.queryByRole('toolbar', { name: 'Selected records' })).toBeNull())  // cleared
  })

  it('the row menu offers the action for one record and greys it with why where it does not fit', async () => {
    page(['x.finish'])
    await screen.findByText('second')
    fireEvent.keyDown(screen.getByRole('button', { name: 'Actions for second' }), { key: 'ArrowDown' })  // a keyboard user
    const item = await screen.findByRole('menuitem', { name: /Finish/ })
    expect(item).toHaveAttribute('aria-disabled', 'true')
    expect(item).toHaveTextContent('Already finished')
  })
})

describe('the words for a run', () => {
  const name = (id: number) => `#${id}`
  const a = { done: (n: number) => `${n} finished` }
  const row = (id: number, status: 'done' | 'skipped' | 'failed', more = {}) => ({ id, status, ...more })
  const out = (...results: ReturnType<typeof row>[]): ActionOut => ({ action: 'x', dry_run: false, results,
    counts: { done: results.filter((r) => r.status === 'done').length, skipped: results.filter((r) => r.status === 'skipped').length, failed: results.filter((r) => r.status === 'failed').length } })

  it('one record: which of the two happened, with its reason; many: counts', () => {
    expect(resultText(a, out(row(1, 'skipped', { reason: 'already finished' })), name)).toBe('0 finished · 1 skipped (already finished)')
    expect(resultText(a, out(row(1, 'failed', { reason: 'changed since you selected it', code: 'entity.stale' })), name)).toBe('0 finished · 1 failed (#1: changed since you selected it)')
    expect(resultText(a, out(row(1, 'done'), row(2, 'skipped', { reason: 'already finished' })), name)).toBe('1 finished · 1 skipped')
  })

  it('what a done one still says is said by name, the first few', () => {
    expect(resultText(a, out(row(1, 'done', { note: 'still in review: no item' })), name)).toBe('1 finished · #1: still in review: no item')
    const many = Array.from({ length: 7 }, (_, i) => row(i + 1, 'done', { note: 'x' }))
    expect(resultText(a, out(...many), name)).toMatch(/#5: x; and 2 more$/)
  })
})
