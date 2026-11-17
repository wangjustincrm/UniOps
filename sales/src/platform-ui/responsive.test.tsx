import { act, fireEvent, render, renderHook, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ActionBar, AutoGrid, Cluster, DetailList, Responsive, Toolbar, columnsOf, useBreakpoint } from './index'
import { DataTable } from './primitives/DataTable'

// The responsive layout primitives (2026-11-16).

function stubWidth(px: number) {
  return vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    width: px, height: 30, top: 0, left: 0, right: px, bottom: 30, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect)
}

function stubMatchMedia(windowWidth: number) {
  const listeners = new Set<() => void>()
  const mm = vi.fn((q: string) => {
    const min = Number(/min-width:\s*(\d+)px/.exec(q)?.[1] ?? 0)
    return {
      matches: windowWidth >= min, media: q, onchange: null,
      addEventListener: (_: string, l: () => void) => listeners.add(l),
      removeEventListener: (_: string, l: () => void) => listeners.delete(l),
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }
  })
  vi.stubGlobal('matchMedia', mm)
  Object.defineProperty(window, 'matchMedia', { value: mm, configurable: true, writable: true })
  return { resize: (w: number) => { windowWidth = w; listeners.forEach((l) => l()) } }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('layout primitives', () => {
  it('Responsive is a container; AutoGrid fits columns of at least `min`, never wider than its box', () => {
    render(<Responsive name="sheet"><AutoGrid min="9rem" aria-label="tiles"><span>a</span><span>b</span></AutoGrid></Responsive>)
    const grid = screen.getByLabelText('tiles')
    expect(grid.parentElement).toHaveClass('@container/sheet', 'min-w-0')
    expect(grid.style.gridTemplateColumns).toBe('repeat(auto-fit, minmax(min(9rem, 100%), 1fr))')
    expect(grid).toHaveClass('grid', 'gap-3')
  })
  it('Cluster wraps', () => {
    render(<Cluster aria-label="chips" justify="between"><span>x</span></Cluster>)
    expect(screen.getByLabelText('chips')).toHaveClass('flex', 'flex-wrap', 'items-center', 'justify-between', 'min-w-0')
  })
  it('ActionBar is a named group, sticky on a phone and a plain row from sm up', () => {
    render(<ActionBar><button>Save</button></ActionBar>)
    const bar = screen.getByRole('group', { name: 'Form actions' })
    expect(bar).toHaveClass('sticky', 'bottom-0', 'sm:static', 'sm:flex-row')
  })
})

describe('Toolbar', () => {
  const items = (onExport: () => void) => [
    { label: 'Send', node: <button>Send</button> },
    { label: 'Export', onSelect: onExport, collapse: true },
    { label: 'Print', onSelect: () => {}, collapse: true },
  ]
  it('wide: every item in the bar, no More menu', () => {
    stubWidth(900)
    render(<Toolbar label="Actions" items={items(() => {})} />)
    const bar = screen.getByRole('toolbar', { name: 'Actions' })
    expect(bar).not.toHaveAttribute('data-narrow')
    expect(screen.getByRole('button', { name: 'Export' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'More actions' })).toBeNull()
  })
  it('narrow: collapsible items move into More (and still work); the others stay', async () => {
    stubWidth(320)
    const onExport = vi.fn()
    render(<Toolbar label="Actions" items={items(onExport)} more={[{ label: 'Archive', onSelect: () => {} }]} />)
    expect(screen.getByRole('toolbar', { name: 'Actions' })).toHaveAttribute('data-narrow', 'true')
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Export' })).toBeNull()
    const more = screen.getByRole('button', { name: 'More actions' })
    fireEvent.keyDown(more, { key: 'Enter' })
    const menu = await screen.findByRole('menu')
    expect([...menu.querySelectorAll('[role="menuitem"]')].map((m) => m.textContent)).toEqual(['Export', 'Print', 'Archive'])
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export' }))
    expect(onExport).toHaveBeenCalledOnce()
  })
})

describe('useBreakpoint', () => {
  it('follows the window: base on a phone, md on a tablet, and changes with it', () => {
    const mm = stubMatchMedia(390)
    const { result } = renderHook(() => useBreakpoint())
    expect(result.current).toBe('base')
    act(() => mm.resize(800))
    expect(result.current).toBe('md')
    act(() => mm.resize(1300))
    expect(result.current).toBe('xl')
  })
})

describe('DataTable card view (narrow)', () => {
  type Row = { id: number; ref: string; status: string; qty: number; city: string }
  const rows: Row[] = [{ id: 1, ref: 'SO-1', status: 'Open', qty: 3, city: 'Calgary' }, { id: 2, ref: 'SO-2', status: 'Done', qty: 5, city: 'Regina' }]
  const col = columnsOf<Row>()
  const columns = [col.text('ref', 'Ref'), col.status('status', 'Status'), col.number('qty', 'Qty'), col.text('city', 'City')]

  it('wide: a table; narrow: one card per row — title, the status as a badge, label: value lines', () => {
    const w = stubWidth(1000)
    const { unmount } = render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} ariaLabel="Orders" />)
    expect(screen.getByRole('table', { name: 'Orders' })).toBeInTheDocument()
    unmount()
    w.mockRestore()
    stubWidth(360)
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} ariaLabel="Orders" />)
    expect(screen.queryByRole('table')).toBeNull()
    const list = screen.getByRole('list', { name: 'Orders' })
    const cards = within(list).getAllByRole('listitem')
    expect(cards).toHaveLength(2)
    expect(cards[0].querySelector('[data-card-title]')).toHaveTextContent('SO-1')
    expect(within(cards[0]).getByText('Open')).toBeInTheDocument()
    expect([...cards[0].querySelectorAll('dt')].map((d) => d.textContent)).toEqual(['Qty', 'City'])
    expect([...cards[0].querySelectorAll('dd')].map((d) => d.textContent)).toEqual(['3', 'Calgary'])
  })
  it('a card opens its row by click and Enter; sorting is a Sort menu', async () => {
    stubWidth(360)
    const open = vi.fn()
    const sort = vi.fn()
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} ariaLabel="Orders" onRowClick={open}
      sortable={['ref', 'qty']} sort={{ id: 'ref', desc: false }} onSortChange={sort} />)
    const [first, second] = within(screen.getByRole('list', { name: 'Orders' })).getAllByRole('listitem')
    fireEvent.click(first)
    fireEvent.keyDown(second, { key: 'Enter' })
    expect(open.mock.calls.map((c) => c[0].id)).toEqual([1, 2])
    fireEvent.keyDown(screen.getByRole('button', { name: 'Sort by' }), { key: 'Enter' })
    const menu = await screen.findByRole('menu')
    expect([...menu.querySelectorAll('[role="menuitem"]')].map((m) => m.textContent)).toEqual(['Ref ↑', 'Qty'])
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Ref ↑' }))
    expect(sort).toHaveBeenCalledWith({ id: 'ref', desc: true })
  })
  it('cardsBelow={false} keeps the table on a phone', () => {
    stubWidth(360)
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} ariaLabel="Orders" cardsBelow={false} />)
    expect(screen.getByRole('table', { name: 'Orders' })).toBeInTheDocument()
  })
})

describe('step 3: pages, details, facts', () => {
  type Row = { id: number; ref: string }
  const rows: Row[] = [{ id: 1, ref: 'A' }]
  const columns = [columnsOf<Row>().text('ref', 'Ref')]
  it('a narrow table pages with one short line (‹ 51–100 of 116 ›), the wide one with the full pager', () => {
    stubWidth(360)
    const onPage = vi.fn()
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} ariaLabel="T"
      pagination={{ page: 2, pageSize: 50, total: 116, onPageChange: onPage, onPageSizeChange: () => {} }} />)
    const nav = screen.getByRole('navigation', { name: 'Pages' })
    expect(nav).toHaveTextContent('51–100 of 116')
    fireEvent.click(within(nav).getByRole('button', { name: 'Next page' }))
    fireEvent.click(within(nav).getByRole('button', { name: 'Previous page' }))
    expect(onPage.mock.calls).toEqual([[3], [1]])
    expect(screen.queryByText('Per page:')).toBeNull()
  })
  it('DetailList follows its own box: stacked when very narrow, a label column from @xs', () => {
    render(<DetailList items={[['Partner', 'Acme']]} />)
    const dl = screen.getByText('Partner').closest('dl')!
    expect(dl.parentElement).toHaveClass('@container')
    expect(dl).toHaveClass('grid-cols-1', '@xs:grid-cols-[minmax(6rem,40%)_minmax(0,1fr)]', '@md:grid-cols-[10rem_minmax(0,1fr)]')
  })
})

describe('step 4: forms', () => {
  it("a form's Save / Cancel sit in the ActionBar (sticky on a phone)", async () => {
    const { SchemaForm } = await import('./index')
    const { z } = await import('zod')
    render(<SchemaForm schema={z.object({ name: z.string() })} fields={[{ name: 'name', label: 'Name', type: 'text' }]}
      defaultValues={{ name: '' }} onSubmit={async () => {}} onCancel={() => {}} />)
    const bar = screen.getByRole('group', { name: 'Form actions' })
    expect(within(bar).getAllByRole('button').map((b) => b.textContent)).toEqual(['Cancel', 'Save'])
    expect(bar).toHaveClass('sticky', 'sm:static')
  })
})

describe('review fixes', () => {
  type Row = { id: number; a: string; b: string; c: string; d: string; e: string }
  const col = columnsOf<Row>()
  const columns = [col.text('a', 'A'), col.text('b', 'B'), col.text('c', 'C'), col.text('d', 'D'), col.text('e', 'E')]
  const rows: Row[] = [{ id: 1, a: 'title', b: 'b1', c: 'c1', d: 'd1', e: 'e1' }]
  it('a clickable card shows its first lines and "N more fields" opens the rest without opening the row', () => {
    stubWidth(360)
    const open = vi.fn()
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} ariaLabel="T" onRowClick={open} cardLines={2} />)
    const card = screen.getByRole('listitem')
    expect([...card.querySelectorAll('dt')].map((d) => d.textContent)).toEqual(['B', 'C'])
    fireEvent.click(within(card).getByRole('button', { name: '2 more fields' }))
    expect([...card.querySelectorAll('dt')].map((d) => d.textContent)).toEqual(['B', 'C', 'D', 'E'])
    expect(open).not.toHaveBeenCalled()
    fireEvent.click(within(card).getByRole('button', { name: 'Fewer fields' }))
    expect(card.querySelectorAll('dt')).toHaveLength(2)
  })
  it('a toolbar on a phone starts collapsed until it is measured', () => {
    stubMatchMedia(390)
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 0 } as DOMRect)
    render(<Toolbar label="Actions" items={[{ label: 'Export', onSelect: () => {}, collapse: true }]} />)
    expect(screen.queryByRole('button', { name: 'Export' })).toBeNull()
  })
})

describe('ActionButton with a disabled reason', () => {
  it('is one focusable button, aria-disabled, does not run, opens its reason; in an ActionBar it is the bar\'s own child (full width on a phone)', async () => {
    const { ActionButton } = await import('./index')
    const run = vi.fn()
    render(<ActionBar><ActionButton disabled disabledReason="Choose a file first." onAction={run}>Upload</ActionButton></ActionBar>)
    const btns = screen.getAllByRole('button', { name: 'Upload' })
    expect(btns).toHaveLength(1)
    expect(btns[0]).toHaveAttribute('aria-disabled', 'true')
    expect(btns[0].parentElement).toHaveAttribute('data-action-bar')
    fireEvent.click(btns[0])
    expect(run).not.toHaveBeenCalled()
    expect(await screen.findByText('Choose a file first.')).toBeInTheDocument()
  })
})

describe('disabled reasons beyond ActionButton', () => {
  it('ConfirmAction: while the required reason is empty, Confirm says what is missing; typed, it confirms', async () => {
    const { ConfirmAction } = await import('./index')
    const onConfirm = vi.fn(async () => {})
    render(<ConfirmAction open onOpenChange={() => {}} title="Cancel it?" confirmLabel="Cancel order" reason={{ label: 'Why it is cancelled' }} onConfirm={onConfirm} />)
    const btn = screen.getByRole('button', { name: 'Cancel order' })
    expect(btn).toHaveAttribute('aria-disabled', 'true')
    expect(btn).toHaveAccessibleDescription('Unavailable: Fill in "Why it is cancelled" first (at least 3 characters).')
    fireEvent.click(btn)
    expect(onConfirm).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText(/Why it is cancelled/), { target: { value: 'customer asked' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel order' }))
    expect(onConfirm).toHaveBeenCalledWith('customer asked')
  })
  it('RowActions / IconButton: a disabled icon with a reason is still reachable and says why', async () => {
    const { RowActions } = await import('./index')
    const run = vi.fn()
    render(<RowActions actions={[{ key: 'run', label: 'Run now', icon: <span>R</span>, disabled: true, disabledReason: 'The connection is inactive.', onAction: run }]} />)
    const btn = screen.getByRole('button', { name: 'Run now' })
    expect(btn).toHaveAttribute('aria-disabled', 'true')
    expect(btn).toHaveAccessibleDescription('Unavailable: Run now: The connection is inactive.')
    fireEvent.click(btn)
    expect(run).not.toHaveBeenCalled()
  })
})

describe('column help (withHelp)', () => {
  type Row = { id: number; ref: string; qty: number }
  const col = columnsOf<Row>()
  it('a ? beside the header in a table, and beside the label in a card', async () => {
    const { HelpProvider, withHelp } = await import('./index')
    const columns = [col.text('ref', 'Ref'), withHelp(col.number('qty', 'On hand'), 'term:On hand')]
    const source = (id: string) => (id === 'term:On hand' ? { title: 'On hand', body: 'At the warehouse now.' } : undefined)
    const ui = <HelpProvider source={source}><DataTable columns={columns} rows={[{ id: 1, ref: 'A', qty: 3 }]} rowKey={(r) => r.id} ariaLabel="T" /></HelpProvider>
    const w = stubWidth(1000)
    const { unmount } = render(ui)
    expect(within(screen.getByRole('table')).getByRole('button', { name: 'What "On hand" means' })).toHaveAttribute('data-help', 'term:On hand')
    unmount(); w.mockRestore()
    stubWidth(360)
    render(ui)
    const card = within(screen.getByRole('list', { name: 'T' })).getByRole('listitem')
    fireEvent.click(within(card).getByRole('button', { name: 'What "On hand" means' }))
    expect(await screen.findByText('At the warehouse now.')).toBeInTheDocument()
  })
})

describe('card help shows once', () => {
  type Row = { id: number; s: number; r: number }
  const col = columnsOf<Row>()
  it('only on the first card, and once per help id within it', async () => {
    const { HelpProvider, withHelp } = await import('./index')
    const columns = [col.text('id', 'Id'), withHelp(col.number('s', 'In'), 'term:In, Out'), withHelp(col.number('r', 'Out'), 'term:In, Out')]
    stubWidth(360)
    render(<HelpProvider source={() => ({ body: 'What came in and went out.' })}>
      <DataTable columns={columns} rows={[{ id: 1, s: 1, r: 0 }, { id: 2, s: 2, r: 0 }]} rowKey={(r) => r.id} ariaLabel="P" />
    </HelpProvider>)
    const [first, second] = within(screen.getByRole('list', { name: 'P' })).getAllByRole('listitem')
    expect(within(first).getAllByRole('button', { name: 'Help' })).toHaveLength(1)
    expect(within(second).queryAllByRole('button', { name: 'Help' })).toHaveLength(0)
  })
})

describe('card help reaches hidden lines', () => {
  type Row = { id: number; a: number; b: number; c: number; d: number }
  const col = columnsOf<Row>()
  it('a ? on a line past cardLines shows on the first card that shows that line (opened)', async () => {
    const { HelpProvider, withHelp } = await import('./index')
    const columns = [col.text('id', 'Id'), col.number('a', 'A'), col.number('b', 'B'), col.number('c', 'C'), withHelp(col.number('d', 'D'), 'term:D')]
    stubWidth(360)
    render(<HelpProvider source={() => ({ body: 'About D.' })}>
      <DataTable columns={columns} rows={[{ id: 1, a: 1, b: 1, c: 1, d: 1 }, { id: 2, a: 2, b: 2, c: 2, d: 2 }]} rowKey={(r) => r.id}
        ariaLabel="L" onRowClick={() => {}} cardLines={2} />
    </HelpProvider>)
    expect(screen.queryAllByRole('button', { name: 'Help' })).toHaveLength(0)  // D is hidden on both cards
    const [, second] = within(screen.getByRole('list', { name: 'L' })).getAllByRole('listitem')
    fireEvent.click(within(second).getByRole('button', { name: '2 more fields' }))
    expect(within(second).getAllByRole('button', { name: 'Help' })).toHaveLength(1)
  })
})

describe('an empty list says why only when nothing narrows it', () => {
  type Row = { id: number; code: string }
  it('no filter: the list\'s own text; a search or filter: "No rows match" and Clear filters (the page\'s fixed filters stay)', async () => {
    const { QueryTable } = await import('./patterns/QueryTable')
    const { useState } = await import('react')
    const seen: unknown[] = []
    function T({ start }: { start: import('./index').QuerySpec }) {
      const [spec, setSpec] = useState(start)
      return <QueryTable<Row> columns={[columnsOf<Row>().text('code', 'Code')]} rows={[]} total={0} spec={spec} locked={['partner']}
        onSpecChange={(s) => { seen.push(s); setSpec(s) }} rowKey={(r) => r.id} emptyTitle="No orders yet" emptyText="Orders arrive on each run." />
    }
    const { unmount } = render(<T start={{ page: 1, pageSize: 20, filters: [{ field: 'partner', op: 'eq', value: 'X' }] }} />)
    expect(screen.getByText('No orders yet')).toBeInTheDocument()
    expect(screen.getByText('Orders arrive on each run.')).toBeInTheDocument()
    unmount()
    render(<T start={{ page: 1, pageSize: 20, search: 'zz', filters: [{ field: 'partner', op: 'eq', value: 'X' }, { field: 'status', op: 'eq', value: 'Open' }] }} />)
    expect(screen.getByText('No rows match the current filters')).toBeInTheDocument()
    expect(screen.queryByText('Orders arrive on each run.')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(seen.at(-1)).toMatchObject({ page: 1, search: undefined, filters: [{ field: 'partner', op: 'eq', value: 'X' }] })
    expect(screen.getByText('Orders arrive on each run.')).toBeInTheDocument()
  })
})

describe('an empty view', () => {
  type Row = { id: number; code: string }
  it('narrowed only by the active view\'s own filters: "Nothing in this view", no Clear; one more filter: "No rows match"', async () => {
    const { QueryTable } = await import('./patterns/QueryTable')
    const hold = [{ field: 'status', op: 'eq' as const, value: 'On Hold' }]
    // the view's stored filter in another key order: compared in the views' one shape, it is the same
    const stored = [{ value: 'On Hold', op: 'eq' as const, field: 'status' }]
    const picker = { system: [{ key: 'hold', name: 'On hold', state: { version: 1, filters: stored } }], mine: [], current: { kind: 'system' as const, key: 'hold', name: 'On hold' },
      dirty: false, stale: false, busy: false, error: null, select: () => {}, save: async () => {}, saveAs: async () => {}, reset: () => {},
      rename: async () => {}, remove: async () => {}, setDefault: async () => {} } as unknown as import('./patterns/views').ViewPickerModel
    const cols = [columnsOf<Row>().text('code', 'Code')]
    const { unmount } = render(<QueryTable<Row> columns={cols} rows={[]} total={0} spec={{ page: 1, pageSize: 20, filters: hold }} picker={picker}
      onSpecChange={() => {}} rowKey={(r) => r.id} emptyTitle="No orders yet" />)
    expect(screen.getByText('Nothing in this view')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull()
    unmount()
    render(<QueryTable<Row> columns={cols} rows={[]} total={0} spec={{ page: 1, pageSize: 20, filters: [...hold, { field: 'code', op: 'eq', value: 'X' }] }} picker={picker}
      onSpecChange={() => {}} rowKey={(r) => r.id} emptyTitle="No orders yet" />)
    expect(screen.getByText('No rows match the current filters')).toBeInTheDocument()
  })
})

describe('list tools own their help', () => {
  it('every tool a list can have has its text; "Using lists" lists only the tools it has', async () => {
    const { PLATFORM_HELP, listTools } = await import('./index')
    const all = listTools({ search: true, panel: true, views: true, export: true })
    for (const id of all) expect(PLATFORM_HELP[id]?.body, id).toBeTruthy()
    expect(Object.keys(PLATFORM_HELP).filter((k) => k.startsWith('list.')).sort()).toEqual([...all].sort())  // and no tool text without a tool
    const bare = listTools({ search: false, panel: false, views: false, export: false })
    expect(bare).not.toContain('list.searchPanel')
    expect(bare).not.toContain('list.export')
    expect(bare).not.toContain('list.views')
    expect(bare).not.toContain('list.search')
  })
})

describe('the list tools\' texts name real buttons', () => {
  const lib = import.meta.glob<string>('./**/*.tsx', { query: '?raw', import: 'default', eager: true })
  const code = Object.entries(lib).filter(([f]) => !f.includes('.test.')).map(([, t]) => t).join('\n')
  // as a label, a title, a menu item, JSX text, or the words after a template's count (`${n} more fields`)
  const isLabel = (w: string) => new RegExp(`(['"\`>]|\\}\\s)${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(['"\`<])`).test(code)
  it('every button a list tool\'s text names exists, by its exact words', async () => {
    const { PLATFORM_HELP } = await import('./index')
    for (const [id, e] of Object.entries(PLATFORM_HELP)) {
      for (const b of e.buttons ?? []) {
        expect(e.body.includes(b), `${id}: names ${b}`).toBe(true)
        expect(isLabel(b), `${id}: ${b} is a real label`).toBe(true)
      }
    }
    expect(isLabel('Send it to the moon')).toBe(false)
  })
})
