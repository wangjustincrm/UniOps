import { act, fireEvent, render, renderHook, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ActionBar, AutoGrid, Cluster, Responsive, Toolbar, columnsOf, useBreakpoint } from './index'
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
