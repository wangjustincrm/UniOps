import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { z } from 'zod'
import {
  ActionButton, ListPage, PermissionProvider, SchemaForm, type FieldSpec, RecordContainerContext, RecordNavContext, RecordSheet, RefLink, ViewsProvider,
  type ColumnDef, type RecordKey, type RecordNav,
} from './index'
import type { ListMeta } from './resource/listMeta'
import type { ViewsClient } from './resource/viewsApi'
import { openMoreActions } from './testing'

// Records and references (user 2026-11-27): a reference is a real link — a click opens the record's own tab,
// a modified click is the browser's, right click offers Open / Open in a new browser tab / Copy link; one record
// view in two containers (a list's sheet, the record's tab); while its tab is open, the sheet is read-only.

function fakeNav(open: RecordKey[] = []) {
  const listeners = new Set<() => void>()
  const opened = [...open]
  const nav: RecordNav & { opens: RecordKey[]; openTab: (r: RecordKey) => void } = {
    opens: [],
    href: (r) => (r.type === 'nopage' ? null : `/${r.type}s/${encodeURIComponent(String(r.key))}`),
    open: (r) => { nav.opens.push(r) },
    isOpen: (r) => opened.some((o) => o.type === r.type && o.key === r.key),
    subscribe: (n) => { listeners.add(n); return () => listeners.delete(n) },
    openTab: (r) => { opened.push(r); listeners.forEach((l) => l()) },
  }
  return nav
}

function wrap(ui: ReactNode, nav: RecordNav, keys = ['app.read', 'app.edit']) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><PermissionProvider can={(k) => keys.includes(k)}>
    <RecordNavContext.Provider value={nav}>{ui}</RecordNavContext.Provider></PermissionProvider></QueryClientProvider>)
}

describe('RefLink', () => {
  it('a real link: a click opens the record\'s tab; Ctrl/⌘, Shift and a middle click are the browser\'s', () => {
    const nav = fakeNav()
    const row = vi.fn()
    wrap(<div onClick={row}><RefLink record={{ type: 'item', key: 'CF 0093' }}>CF 0093</RefLink></div>, nav)
    const link = screen.getByRole('link', { name: 'CF 0093' })
    expect(link).toHaveAttribute('href', '/items/CF%200093')
    expect(fireEvent.click(link)).toBe(false)  // the app's: not the browser's navigation
    expect(nav.opens).toEqual([{ type: 'item', key: 'CF 0093' }])
    const jsdomCannotNavigate = vi.spyOn(console, 'error').mockImplementation(() => {})
    const left = [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { button: 1 }].map((mod) => fireEvent.click(link, mod))
    jsdomCannotNavigate.mockRestore()
    expect(left).toEqual([true, true, true, true])  // not prevented: the browser opens them
    expect(nav.opens).toHaveLength(1)
    expect(row).not.toHaveBeenCalled()  // a link in a row never opens the row's sheet too
  })

  it('right click, or Shift+F10 on it: Open, Open in a new browser tab, Copy link', async () => {
    const nav = fakeNav()
    const write = vi.fn(async () => {})
    Object.assign(navigator, { clipboard: { writeText: write } })
    const win = vi.spyOn(window, 'open').mockImplementation(() => null)
    wrap(<RefLink record={{ type: 'lot', key: 'L1' }}>L1</RefLink>, nav)
    const link = screen.getByRole('link', { name: 'L1' })
    fireEvent.contextMenu(link, { clientX: 10, clientY: 10 })
    let menu = await screen.findByRole('menu')
    expect(within(menu).getAllByRole('menuitem').map((m) => m.textContent)).toEqual(['Open', 'Open in a new browser tabCtrl+click or a middle click', 'Copy link'])
    fireEvent.click(within(menu).getByRole('menuitem', { name: /^Copy link/ }))
    await waitFor(() => expect(write).toHaveBeenCalledWith(`${window.location.origin}/lots/L1`))
    fireEvent.keyDown(link, { key: 'F10', shiftKey: true })
    menu = await screen.findByRole('menu')
    fireEvent.click(within(menu).getByRole('menuitem', { name: /^Open in a new browser tab/ }))
    expect(win).toHaveBeenCalledWith(`${window.location.origin}/lots/L1`, '_blank', 'noopener')
    fireEvent.contextMenu(link)
    fireEvent.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Open' }))
    expect(nav.opens).toEqual([{ type: 'lot', key: 'L1' }])
    win.mockRestore()
  })

  it('not readable by this person, no page for its type, or no key: its text only', () => {
    wrap(<>
      <span><RefLink record={{ type: 'item', key: 'A' }} readable={false}>A</RefLink></span>
      <span><RefLink record={{ type: 'nopage', key: 'B' }}>B</RefLink></span>
      <span><RefLink record={{ type: 'item', key: '' }}>C</RefLink></span>
    </>, fakeNav())
    expect(screen.queryAllByRole('link')).toEqual([])
    expect(screen.getByText('A')).toBeInTheDocument()
  })
})

const noteForm = { schema: z.object({ note: z.string() }), fields: [{ name: 'note', label: 'Note' }] as FieldSpec[], defaultValues: { note: 'x' }, onSubmit: async () => {} }
const tabs = [{ id: 'd', label: 'Details', render: () => <><ActionButton permission="app.edit" onAction={() => {}}>Save</ActionButton><SchemaForm {...noteForm} /></> }]

describe('one record view, two containers', () => {
  it('in a list\'s sheet: "Open in its tab" is its first button; it closes the sheet and opens the tab', async () => {
    const nav = fakeNav()
    const close = vi.fn()
    wrap(<RecordSheet title="CF0093" record={{ type: 'item', key: 'CF0093' }} open onOpenChange={close} tabs={tabs}
      bar={[{ id: 'edit', label: 'Edit item', primary: 10, onSelect: () => {} }]} />, nav)
    const dialog = await screen.findByRole('dialog', { name: 'CF0093' })
    const bar = within(dialog).getByRole('toolbar', { name: 'Record actions' })
    expect(within(bar).getAllByRole('button').map((b) => b.textContent)).toEqual(['Open in its tab', 'Edit item', ''])  // two, then ⋯
    expect(within(await openMoreActions(dialog)).getByRole('menuitem', { name: 'Copy link' })).toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
    fireEvent.click(within(bar).getByRole('button', { name: 'Open in its tab' }))
    await waitFor(() => expect(nav.opens).toEqual([{ type: 'item', key: 'CF0093' }]))
    expect(close).toHaveBeenCalledWith(false)
  })

  it('while its tab is open the sheet is read-only: it says so, offers its tab, and nothing in it changes the record', async () => {
    const nav = fakeNav([{ type: 'item', key: 'CF0093' }])
    wrap(<RecordSheet title="CF0093" record={{ type: 'item', key: 'CF0093' }} open onOpenChange={() => {}} tabs={tabs}
      bar={[{ id: 'edit', label: 'Edit item', primary: 10, onSelect: () => {} }]} />, nav)
    const dialog = await screen.findByRole('dialog', { name: 'CF0093' })
    expect(within(dialog).getByRole('region', { name: 'Open in its tab' })).toHaveTextContent('CF0093 is open in its own tab: change it there.')
    expect(within(dialog).queryByRole('button', { name: 'Edit item' })).toBeNull()
    expect(within(dialog).queryByRole('button', { name: 'Open in its tab' })).toBeNull()
    expect(within(dialog).getByRole('button', { name: 'Save' })).toHaveAccessibleDescription(/Open in its tab to change it/)
    expect(within(dialog).getByLabelText('Note')).toBeDisabled()  // a form in it too: read-only
    fireEvent.click(within(dialog).getByRole('button', { name: 'Go to its tab' }))
    expect(nav.opens).toEqual([{ type: 'item', key: 'CF0093' }])
  })

  it('the sheet turns read-only the moment its tab opens elsewhere', async () => {
    const nav = fakeNav()
    wrap(<RecordSheet title="CF0093" record={{ type: 'item', key: 'CF0093' }} open onOpenChange={() => {}} tabs={tabs} />, nav)
    const dialog = await screen.findByRole('dialog', { name: 'CF0093' })
    expect(within(dialog).queryByRole('region', { name: 'Open in its tab' })).toBeNull()
    act(() => nav.openTab({ type: 'item', key: 'CF0093' }))
    expect(await within(dialog).findByRole('region', { name: 'Open in its tab' })).toBeInTheDocument()
  })

  it('in its own tab: a page (no dialog), "← Back to …", no Open in its tab, editable', async () => {
    const nav = fakeNav([{ type: 'item', key: 'CF0093' }])  // this very tab
    const close = vi.fn()
    wrap(<RecordContainerContext.Provider value={{ kind: 'tab', close, back: <a href="/orders/5">← Back to Order SHP-9649</a> }}>
      <RecordSheet title="CF0093" record={{ type: 'item', key: 'CF0093' }} tabs={tabs} />
    </RecordContainerContext.Provider>, nav)
    expect(screen.queryByRole('dialog')).toBeNull()
    const page = screen.getByRole('region', { name: 'CF0093' })
    expect(within(page).getByRole('heading', { name: 'CF0093' })).toBeInTheDocument()
    expect(within(page).getByRole('link', { name: '← Back to Order SHP-9649' })).toBeInTheDocument()
    expect(within(page).queryByRole('button', { name: 'Open in its tab' })).toBeNull()
    expect(within(page).queryByRole('region', { name: 'Open in its tab' })).toBeNull()
    expect(within(page).getAllByRole('button', { name: 'Save' })[0]).not.toHaveAttribute('aria-describedby')
    expect(within(page).getByLabelText('Note')).toBeEnabled()
    expect(within(await openMoreActions(page)).getByRole('menuitem', { name: 'Copy link' })).toBeInTheDocument()
  })
})

type Row = { id: number; version: number; lot: string; item_code: string; order_ref: string | null; order_id: number | null }
const field = (name: string, ref?: { type: string; key: string }) => ({ name, label: name, type: 'text' as const, enum_values: [], filterable: true, sortable: true,
  groupable: false, operators: ['eq' as const], in_row: true, ...(ref ? { ref } : {}) })
const columns: ColumnDef<Row, unknown>[] = [
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => <b>{row.original.lot}</b> },
  { accessorKey: 'item_code', header: 'Item' },
  { accessorKey: 'order_ref', header: 'Order ref' },
]
const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }

describe('a list field that names a record is a link (declared on the server, /meta ref)', () => {
  it('its cell as it is, inside the link, keyed by the declared field; not readable: the text; no key: the text', async () => {
    const meta: ListMeta = { version: 1, searchable: false, default_sort: [], fields: [
      field('lot', { type: 'lot', key: 'lot' }), field('item_code', { type: 'item', key: 'item_code' }), field('order_ref', { type: 'order', key: 'order_id' })],
      refs: { lot: { readable: true }, item: { readable: false }, order: { readable: true } } }
    const rows: Row[] = [{ id: 1, version: 1, lot: 'L1', item_code: 'CF0093', order_ref: 'SHP-9649', order_id: 5 },
      { id: 2, version: 1, lot: 'L2', item_code: 'CF0092', order_ref: 'SHP-1', order_id: null }]
    const nav = fakeNav()
    const onRow = vi.fn()
    wrap(<ViewsProvider client={views}><ListPage<Row> resource={{ basePath: '/lots', meta: async () => meta, list: async () => ({ items: rows, total: 2 }) }}
      columns={columns} onRowClick={onRow} /></ViewsProvider>, nav)
    const lot = await screen.findByRole('link', { name: 'L1' })
    expect(lot).toHaveAttribute('href', '/lots/L1')
    expect(lot.querySelector('b')).not.toBeNull()  // the page's own cell, inside
    expect(screen.getByRole('link', { name: 'SHP-9649' })).toHaveAttribute('href', '/orders/5')
    expect(screen.queryByRole('link', { name: 'SHP-1' })).toBeNull()      // no order id
    expect(screen.queryByRole('link', { name: 'CF0093' })).toBeNull()     // items: not readable by this person
    fireEvent.click(lot)
    expect(nav.opens).toEqual([{ type: 'lot', key: 'L1' }])
    expect(onRow).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('CF0093'))
    expect(onRow).toHaveBeenCalledTimes(1)
  })
})
