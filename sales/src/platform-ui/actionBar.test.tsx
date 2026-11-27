import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, RecordActionBar, ReadOnlyContext, arrangeActions, type BarItem } from './index'
import { openMoreActions } from './testing'

// THE action bar (user 2026-11-27): declared once, laid out by one rule — at most two buttons, the most important
// actions that fit now; the rest in "⋯", the record's own first, a line, the built-in ones; a destructive one last
// of its group; not the person's: not shown; not fitting: greyed with why, or hidden as declared; read-only: only
// what changes nothing; a phone: everything in "⋯".

const can = (k: string) => k !== 'nope'
const item = (id: string, over: Partial<BarItem> = {}): BarItem => ({ id, label: id, onSelect: () => {}, ...over })
const ids = (xs: { id: string }[]) => xs.map((x) => x.id)

describe('arrange', () => {
  it('two buttons at most: the most important that fit now (a greyed one is never a button)', () => {
    const { buttons, menu } = arrangeActions([
      item('c', { primary: 30 }), item('a', { primary: 5, fit: 'On hold' }), item('b', { primary: 10 }), item('d', { primary: 40 }), item('e'),
    ], { can })
    expect(ids(buttons)).toEqual(['b', 'c'])
    expect(ids(menu)).toEqual(['a', 'd', 'e'])
    expect(menu[0].disabledReason).toBe('On hold')
  })

  it('the record\'s own first, then a line and the built-in ones; a destructive action last of its group', () => {
    const { menu } = arrangeActions([
      item('copy', { group: 'builtin' }), item('cancel', { tone: 'destructive' }), item('amend', { order: 2 }), item('edit', { order: 1 }),
      item('refresh', { group: 'builtin' }),
    ], { can })
    expect(ids(menu)).toEqual(['edit', 'amend', 'cancel', 'copy', 'refresh'])
    expect(menu.map((m) => !!m.separated)).toEqual([false, false, false, true, false])
  })

  it('not the person\'s: not shown; not fitting and declared hidden: not shown (a pair shows one)', () => {
    const { buttons, menu } = arrangeActions([
      item('settle', { primary: 40, fit: 'Already settled', whenNotFit: 'hidden' }), item('unsettle', { tone: 'destructive', whenNotFit: 'hidden' }),
      item('secret', { permission: 'nope', primary: 1 }),
    ], { can })
    expect(ids(buttons)).toEqual([])
    expect(ids(menu)).toEqual(['unsettle'])
  })

  it('read-only (its tab is open): only what changes nothing; a phone: no buttons, all in "⋯"', () => {
    const items = [item('save', { primary: 1 }), item('copy', { group: 'builtin' }), item('pdf', { changes: false }), item('open', { group: 'builtin', primary: 0 })]
    expect(ids(arrangeActions(items, { can, readOnly: true }).menu)).toEqual(['pdf', 'copy'])
    const phone = arrangeActions(items, { can, phone: true })
    expect(phone.buttons).toEqual([])
    expect(ids(phone.menu)).toEqual(['save', 'pdf', 'copy', 'open'])
  })
})

function show(items: BarItem[], readOnly: string | null = null) {
  const qc = new QueryClient()
  return render(<QueryClientProvider client={qc}><PermissionProvider can={can}>
    <ReadOnlyContext.Provider value={readOnly}><RecordActionBar items={items} /></ReadOnlyContext.Provider>
  </PermissionProvider></QueryClientProvider>)
}

describe('RecordActionBar', () => {
  it('a button runs once while it runs; "⋯" holds the rest with the greyed one\'s reason', async () => {
    let finish: () => void = () => {}
    const run = vi.fn(() => new Promise<void>((r) => { finish = r }))
    show([item('send', { label: 'Send now', primary: 10, onSelect: run }), item('x', { label: 'Replace…', fit: 'Nothing live' }),
      item('copy', { label: 'Copy link', group: 'builtin' })])
    const button = screen.getByRole('button', { name: 'Send now' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(run).toHaveBeenCalledTimes(1)
    expect(button).toHaveAttribute('aria-busy', 'true')
    finish()
    await waitFor(() => expect(button).not.toHaveAttribute('aria-busy'))
    const menu = await openMoreActions()
    expect(within(menu).getByRole('menuitem', { name: /Replace…/ })).toHaveAttribute('data-disabled')
    expect(within(menu).getByText('Nothing live')).toBeInTheDocument()
    expect(within(menu).getAllByRole('separator')).toHaveLength(1)
  })

  it('nothing to offer: nothing at all (no empty "⋯")', () => {
    show([item('secret', { permission: 'nope', primary: 1 })])
    expect(screen.queryByRole('toolbar')).toBeNull()
  })

  it('read-only: what changes the record is not offered', async () => {
    show([item('save', { label: 'Mark settled', primary: 1 }), item('copy', { label: 'Copy link', group: 'builtin' })], 'Open in its tab to change it')
    expect(screen.queryByRole('button', { name: 'Mark settled' })).toBeNull()
    expect(within(await openMoreActions()).getAllByRole('menuitem').map((m) => m.textContent)).toEqual(['Copy link'])
  })
})
