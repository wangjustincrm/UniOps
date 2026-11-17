import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DisabledReason, HelpProvider, HelpTip, useHelp } from './index'
import type { HelpEntry } from './types'

// The help module (the user, 2026-11-16): on its own, with a module's texts given to it.

const TEXTS: Record<string, HelpEntry> = {
  'status:Sent': { title: 'Sent', body: 'The order is with the warehouse.', next: 'Wait for its answer.' },
}
const source = (id: string) => TEXTS[id]

describe('HelpTip', () => {
  it('a ? opens the text by tap or click (no hover needed), marked for a tour, and closes on Esc', async () => {
    render(<HelpProvider source={source}><HelpTip id="status:Sent" /></HelpProvider>)
    const tip = screen.getByRole('button', { name: 'What "Sent" means' })
    expect(tip).toHaveAttribute('data-help', 'status:Sent')
    fireEvent.click(tip)
    const box = await screen.findByRole('dialog')
    expect(box).toHaveTextContent('Sent')
    expect(box).toHaveTextContent('The order is with the warehouse.')
    expect(box).toHaveTextContent('Next: Wait for its answer.')
    fireEvent.keyDown(box, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('children are the trigger (a badge you tap); no text → just the children, and the module is told', () => {
    const missing = vi.fn()
    render(
      <HelpProvider source={source} onMissing={missing}>
        <HelpTip id="status:Sent"><span>Sent</span></HelpTip>
        <HelpTip id="status:nope"><span>Plain</span></HelpTip>
        <HelpTip id="status:nope2" />
      </HelpProvider>,
    )
    expect(screen.getByRole('button', { name: 'What "Sent" means' })).toHaveTextContent('Sent')
    expect(screen.getByText('Plain').closest('button')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Help' })).toBeNull()
    expect(missing.mock.calls.map((c) => c[0])).toEqual(['status:nope', 'status:nope2'])
  })
  it('an entry needs no provider; with neither, nothing is shown', () => {
    const { container } = render(<><HelpTip entry={{ body: 'Inline text' }} label="About this" /><HelpTip id="status:x" /></>)
    expect(screen.getByRole('button', { name: 'About this' })).toBeInTheDocument()
    expect(container.querySelectorAll('button')).toHaveLength(1)
  })
  it('useHelp reads the provider', () => {
    function Line() { return <p>{useHelp('status:Sent')?.body}</p> }
    render(<HelpProvider source={source}><Line /></HelpProvider>)
    expect(screen.getByText('The order is with the warehouse.')).toBeInTheDocument()
  })
})

describe('DisabledReason', () => {
  it('a disabled control says why: still focusable and tappable, aria-disabled, the reason as its description', async () => {
    render(<DisabledReason reason="Only a Draft can be submitted."><button type="button">Submit</button></DisabledReason>)
    const btn = screen.getByRole('button', { name: 'Submit' })
    expect(btn).not.toBeDisabled()
    expect(btn).toHaveAttribute('aria-disabled', 'true')
    expect(btn).toHaveAccessibleDescription('Unavailable: Only a Draft can be submitted.')
    fireEvent.click(btn)
    expect(within(await screen.findByRole('dialog')).getByText('Only a Draft can be submitted.')).toBeInTheDocument()
  })
  it("the child's own action never runs while a reason is shown, and it can't submit", async () => {
    const act = vi.fn()
    const submit = vi.fn((e: { preventDefault: () => void }) => e.preventDefault())
    render(<form onSubmit={submit}><DisabledReason reason="Not now."><button type="submit" onClick={act}>Save</button></DisabledReason></form>)
    const btn = screen.getByRole('button', { name: 'Save' })
    expect(btn).toHaveAttribute('type', 'button')
    fireEvent.click(btn)
    expect(act).not.toHaveBeenCalled()
    expect(submit).not.toHaveBeenCalled()
    expect(await screen.findByText('Not now.')).toBeInTheDocument()
  })
  it('no reason: the control alone', () => {
    const { container } = render(<DisabledReason reason={null}><button>Go</button></DisabledReason>)
    expect(container.querySelector('[data-disabled-reason]')).toBeNull()
  })
})

describe('search and the page panel', () => {
  const all = [
    { id: 'term:EA', entry: { title: 'EA — term', body: 'Each: the unit everything is counted in.' } },
    { id: 'status:Sent', entry: { title: 'Sent — order status', body: 'Waiting for its answer from the warehouse.' } },
    { id: 'page:orders', entry: { title: 'Orders', body: 'Every order.', tasks: ['Find an order: search its ref.'] } },
  ]
  it('finds texts holding every word, titles first, case and accents ignored; empty finds nothing', async () => {
    const { searchHelp } = await import('./index')
    expect(searchHelp(all, '')).toEqual([])
    expect(searchHelp(all, 'warehouse').map((h) => h.id)).toEqual(['status:Sent'])
    expect(searchHelp(all, 'ORDER').map((h) => h.id)).toEqual(['status:Sent', 'page:orders'])  // both titles hold it
    expect(searchHelp(all, 'each unit').map((h) => h.id)).toEqual(['term:EA'])
    expect(searchHelp(all, 'search ref').map((h) => h.id)).toEqual(['page:orders'])  // its tasks count
    expect(searchHelp(all, 'nope')).toEqual([])
  })
  it("a page's ? opens its panel: what it is for, its tasks, and a search over every text", async () => {
    const { PageHelp } = await import('./index')
    const source = (id: string) => all.find((h) => h.id === id)?.entry
    render(<HelpProvider source={source} catalog={() => all}><PageHelp id="page:orders" page="Orders" /><PageHelp id="page:none" page="None" /></HelpProvider>)
    expect(screen.queryByRole('button', { name: 'Help: None' })).toBeNull()  // no text: no button
  })
  it('the panel lists the page and searches', async () => {
    const { PageHelp } = await import('./index')
    const entries = { 'page:orders': all[2].entry } as Record<string, HelpEntry>
    render(<HelpProvider source={(id) => entries[id]} catalog={() => all}><PageHelp id="page:orders" page="Orders" /></HelpProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Help: Orders' }))
    const panel = await screen.findByRole('dialog', { name: 'Help: Orders' })
    expect(within(panel).getByText('Every order.')).toBeInTheDocument()
    expect(within(panel).getByText('Find an order: search its ref.')).toBeInTheDocument()
    fireEvent.change(within(panel).getByRole('searchbox', { name: 'Search help' }), { target: { value: 'warehouse' } })
    expect(within(within(panel).getByRole('region', { name: 'Search results' })).getByText('Sent — order status')).toBeInTheDocument()
    fireEvent.change(within(panel).getByRole('searchbox', { name: 'Search help' }), { target: { value: 'zzz' } })
    expect(within(panel).getByText('Nothing found for “zzz”.')).toBeInTheDocument()
  })
})

describe('search ignores one-letter words', () => {
  it('"a" alone finds nothing; with a real word it is ignored', async () => {
    const { searchHelp } = await import('./index')
    const all = [{ id: 'term:EA', entry: { title: 'EA', body: 'Each: a unit.' } }, { id: 'term:SSCC', entry: { title: 'SSCC', body: 'A pallet label.' } }]
    expect(searchHelp(all, 'a')).toEqual([])
    expect(searchHelp(all, 'a pallet').map((h) => h.id)).toEqual(['term:SSCC'])
  })
})

describe('layers', () => {
  it('a later layer overrides an earlier one, the source overrides both; the catalog merges without duplicates', async () => {
    const { useHelp } = await import('./index')
    const base = { 'list.search': { title: 'Search', body: 'Base text.' }, 'list.views': { title: 'Views', body: 'Views text.' } }
    const mine = { 'list.search': { title: 'Search', body: 'My text.' } }
    let cat: { id: string }[] = []
    function Probe() {
      const ctx = useHelp('list.search'); const v = useHelp('list.views'); const s = useHelp('status:Sent')
      return <p>{ctx?.body}|{v?.body}|{s?.body}</p>
    }
    function Cat() { const c = (useContextCatalog()); cat = c; return null }
    const { HelpContext } = await import('./context')
    const { useContext } = await import('react')
    function useContextCatalog() { return useContext(HelpContext)!.catalog!() }
    render(<HelpProvider layers={[base, mine]} source={(id) => (id === 'status:Sent' ? { body: 'From the API.' } : undefined)}
      catalog={() => [{ id: 'status:Sent', entry: { body: 'From the API.' } }]}><Probe /><Cat /></HelpProvider>)
    expect(screen.getByText('My text.|Views text.|From the API.')).toBeInTheDocument()
    expect(cat.map((h) => h.id).sort()).toEqual(['list.search', 'list.views', 'status:Sent'])
  })
  it('the page panel lists a section\'s tips (e.g. Using lists) among its own', async () => {
    const { PageHelp } = await import('./index')
    const texts = { 'page:x': { body: 'The page.' }, 'list.search': { title: 'Search', body: 'Type and press Enter.' } }
    render(<HelpProvider layers={[texts]}><PageHelp id="page:x" page="X" sections={[{ title: 'Using lists', ids: ['list.search', 'list.none'] }]} /></HelpProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Help: X' }))
    const panel = await screen.findByRole('dialog', { name: 'Help: X' })
    await waitFor(() => expect(panel.querySelector('[data-help-row="list.search"]')?.textContent).toContain('Type and press Enter.'))
    expect(panel.querySelector('[data-help-row="list.none"]')).toBeNull()  // no text: no tip
  })
})

describe('the tour', () => {
  afterEach(() => vi.restoreAllMocks())
  // every marked element has a box, except "list.b" (not shown)
  const boxes = () => vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const w = this.getAttribute('data-help') === 'list.b' ? 0 : 40
    return { width: w, height: w ? 20 : 0, top: 10, left: 10, right: 10 + w, bottom: 30, x: 10, y: 10, toJSON: () => ({}) } as DOMRect
  })
  it('walks its steps (Next / Back / Done), skips a step whose element is not shown, and Esc ends it', async () => {
    const { Tour } = await import('./index')
    const texts = { 'list.a': { title: 'A', body: 'First.' }, 'list.c': { title: 'C', body: 'Third.' }, 'status:Sent': { title: 'Sent', body: 'Badge.' } }
    const ended = vi.fn()
    boxes()
    render(<HelpProvider layers={[texts]}>
      <span data-help="list.a">a</span><span data-help="list.b">b</span><span data-help="list.c">c</span><span data-help="status:Sent">s</span>
      <Tour steps={[{ anchor: 'list.a' }, { anchor: 'list.b' }, { anchor: 'list.c' }, { anchor: 'status:*' }]} onEnd={ended} />
    </HelpProvider>)
    const step = async () => (await screen.findByRole('dialog')).textContent
    await waitFor(async () => expect(await step()).toContain('First.'))
    expect(await step()).toContain('1 of 3')  // counts only the steps shown (b is not)
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(async () => expect(await step()).toContain('Third.'))  // b skipped
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    await waitFor(async () => expect(await step()).toContain('First.'))  // b skipped going back too
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(async () => expect(await step()).toContain('Third.'))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(async () => expect(await step()).toContain('Badge.'))  // "status:*": the first status badge
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(ended).toHaveBeenCalledOnce()
  })
  it('a step whose element goes away mid-tour moves on (never lights a box at the corner)', async () => {
    const { Tour } = await import('./index')
    boxes()
    function Page({ gone }: { gone: boolean }) {
      return <HelpProvider layers={[{ 'list.a': { body: 'First.' }, 'list.c': { body: 'Third.' } }]}>
        {!gone && <span data-help="list.a">a</span>}<span data-help="list.c">c</span>
        <Tour steps={[{ anchor: 'list.a' }, { anchor: 'list.c' }]} onEnd={() => {}} />
      </HelpProvider>
    }
    const { rerender } = render(<Page gone={false} />)
    await waitFor(async () => expect((await screen.findByRole('dialog')).textContent).toContain('First.'))
    rerender(<Page gone />)
    await waitFor(() => expect(screen.getByRole('dialog').textContent).toContain('Third.'))
  })
  it('Esc ends it', async () => {
    const { Tour } = await import('./index')
    const ended = vi.fn()
    boxes()
    render(<HelpProvider layers={[{ 'list.a': { body: 'First.' } }]}><span data-help="list.a">a</span><Tour steps={[{ anchor: 'list.a' }]} onEnd={ended} /></HelpProvider>)
    await screen.findByRole('dialog')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(ended).toHaveBeenCalled()
  })
})

describe('the tour manager', () => {
  afterEach(async () => { vi.restoreAllMocks(); (await import('./index')).seenStore.reset() })
  const boxes = () => vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const w = this.getAttribute('data-hidden') ? 0 : 40
    return { width: w, height: w ? 20 : 0, top: 10, left: 10, right: 10 + w, bottom: 30, x: 10, y: 10, toJSON: () => ({}) } as DOMRect
  })
  it('stops register in their nearest scope; planned by slot (named, like a z-index), one per key, hidden ones marked', async () => {
    const { TourScope, TourMark, planStops } = await import('./index')
    const { ScopeContext } = await import('./tourContext')
    const { useContext } = await import('react')
    boxes()
    let scope: import('./tourContext').ScopeValue | null = null
    function Grab() { scope = useContext(ScopeContext); return null }
    render(<TourScope id="p"><Grab />
      <TourMark help="list.refresh" slot="list.refresh">r</TourMark>
      <TourMark help="status:Sent" slot="row.status" tourKey="status">s1</TourMark>
      <TourMark help="status:Shipped" slot="row.status" tourKey="status">s2</TourMark>
      <TourMark help="list.views" slot="list.views">v</TourMark>
      <TourScope id="r" kind="record"><TourMark help="record.facts" slot="record.facts">inner scope</TourMark></TourScope>
    </TourScope>)
    const plan = planStops(scope!.stops())
    expect(plan.map((p) => [p.slot, p.help])).toEqual([['list.views', 'list.views'], ['row.status', 'status:Sent'], ['list.refresh', 'list.refresh']])
  })
  it('"?" opens the help of the page in view (not while typing); a first visit shows "New here? Show me" while it has stops', async () => {
    const { PageHelp, TourManager, TourScope, TourMark } = await import('./index')
    boxes()
    render(<HelpProvider layers={[{ 'page:x': { body: 'The page.' }, 'list.views': { body: 'Views.' } }]}><TourManager><TourScope id="x">
      <input aria-label="Field" /><PageHelp id="page:x" page="X" /><TourMark help="list.views" slot="list.views">v</TourMark>
    </TourScope></TourManager></HelpProvider>)
    expect(await screen.findByRole('button', { name: 'New here? Show me' }, { timeout: 2000 })).toBeInTheDocument()
    fireEvent.keyDown(screen.getByLabelText('Field'), { key: '?' })
    expect(screen.queryByRole('dialog', { name: 'Help: X' })).toBeNull()
    fireEvent.keyDown(document.body, { key: '?' })
    expect(await screen.findByRole('dialog', { name: 'Help: X' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New here? Show me' })).toBeNull()  // seen
  })
  it('"Show me" walks the page\'s registered stops; no stops: no "Show me"', async () => {
    const { PageHelp, TourManager, TourScope, TourMark } = await import('./index')
    boxes()
    const texts = { 'page:x': { body: 'The page.' }, 'page:y': { body: 'Empty.' }, 'list.views': { title: 'Views', body: 'Views text.' } }
    render(<HelpProvider layers={[texts]}><TourManager>
      <TourScope id="x"><PageHelp id="page:x" page="X" /><TourMark help="list.views" slot="list.views">v</TourMark></TourScope>
      <TourScope id="y"><PageHelp id="page:y" page="Y" /></TourScope>
    </TourManager></HelpProvider>)
    await new Promise((r) => setTimeout(r, 350))
    fireEvent.click(screen.getByRole('button', { name: 'Help: Y' }))
    const empty = await screen.findByRole('dialog', { name: 'Help: Y' })
    await new Promise((r) => setTimeout(r, 350))
    expect(within(empty).queryByRole('button', { name: /^Show me around this page/ })).toBeNull()
    fireEvent.keyDown(empty, { key: 'Escape' })
    fireEvent.click(screen.getByRole('button', { name: 'Help: X' }))
    const panel = await screen.findByRole('dialog', { name: 'Help: X' })
    await waitFor(() => expect(within(panel).getByRole('button', { name: /^Show me around this page/ })).toBeInTheDocument())
    fireEvent.click(within(panel).getByRole('button', { name: /^Show me around this page/ }))
    await waitFor(() => expect(document.querySelector('[data-tour-step]')?.textContent).toContain('Views text.'))
  })
  it('the inspector lists the page\'s stops in order, and says which are hidden or have no text', async () => {
    const { Inspector } = await import('./index')
    const el = document.createElement('span')
    render(<Inspector plan={[{ key: 'a', slot: 'list.views', order: 10, help: 'list.views', el, shown: true },
      { key: 'b', slot: 'list.export', order: 70, help: 'list.export', el, shown: false }]} text={(h) => h === 'list.views'} />)
    fireEvent.click(screen.getByText('Tour inspector (2 stops)'))
    expect(screen.getByText('hidden now')).toBeInTheDocument()
    expect(screen.getByText('no text')).toBeInTheDocument()
  })
  it('Esc during a tour inside a sheet ends the tour only: the sheet stays open', async () => {
    const { Tour } = await import('./index')
    boxes()
    const closed = vi.fn()
    const ended = vi.fn()
    function Sheet() {
      return <div role="dialog" aria-label="Sheet" onKeyDown={(e) => { if (e.key === 'Escape') closed() }}>
        <span data-help="list.a">a</span>
        <Tour steps={[{ anchor: 'list.a' }]} onEnd={ended} />
      </div>
    }
    render(<HelpProvider layers={[{ 'list.a': { body: 'First.' } }]}><Sheet /></HelpProvider>)
    await screen.findByText('First.')
    fireEvent.keyDown(screen.getByText('a'), { key: 'Escape' })
    expect(ended).toHaveBeenCalled()
    expect(closed).not.toHaveBeenCalled()
  })
  it('a search result that is on the page has Show, which lights it', async () => {
    const { PageHelp, TourManager, TourScope } = await import('./index')
    boxes()
    const texts = { 'page:x': { body: 'The page.' }, 'list.refresh': { title: 'Refresh', body: 'Reads the list again.' }, 'list.export': { title: 'Export CSV', body: 'Exports every row of the list.' } }
    render(<HelpProvider layers={[texts]}><TourManager><TourScope id="x"><span data-help="list.refresh">r</span><PageHelp id="page:x" page="X" /></TourScope></TourManager></HelpProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Help: X' }))
    const panel = await screen.findByRole('dialog', { name: 'Help: X' })
    fireEvent.change(within(panel).getByRole('searchbox', { name: 'Search help' }), { target: { value: 'list' } })
    const results = within(panel).getByRole('region', { name: 'Search results' })
    const refresh = within(results).getByText('Refresh').closest('article')!
    const exp = within(results).getByText('Export CSV').closest('article')!
    expect(within(exp).queryByRole('button', { name: 'Show' })).toBeNull()  // not on this page
    fireEvent.click(within(refresh).getByRole('button', { name: 'Show' }))
    await waitFor(() => expect(document.querySelector('[data-tour-step]')?.textContent).toContain('Reads the list again.'))
  })
})

describe('the slot table is the one place for order', () => {
  it('every slot a component names is in the table; no raw tour numbers anywhere', async () => {
    const { DEFAULT_SLOTS } = await import('./index')
    const files = import.meta.glob<string>('../../**/*.tsx', { query: '?raw', import: 'default', eager: true })
    const code = Object.entries(files).filter(([f]) => !f.includes('.test.')).map(([, t]) => t).join('\n')
    const named = new Set([...code.matchAll(/(?:slot|tourSlot)[=:]\s*["'{ ]*['"]([a-z]+\.[a-zA-Z]+)['"]/g)].map((m) => m[1]))
    expect(named.size).toBeGreaterThan(8)
    for (const slot of named) expect(DEFAULT_SLOTS[slot], slot).toBeDefined()
    expect(code).not.toMatch(/data-tour=\{\d|data-tour="\d|tour=\{\d/)
  })
})

describe('seen per stop, what is new, the drawer and the catalog', () => {
  afterEach(async () => { vi.restoreAllMocks(); (await import('./index')).seenStore.reset() })
  const boxes = () => vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
    return { width: 40, height: 20, top: 10, left: 10, right: 50, bottom: 30, x: 10, y: 10, toJSON: () => ({}) } as DOMRect
  })
  const texts = {
    'page:a': { body: 'Page A.' }, 'page:b': { body: 'Page B.' },
    'list.views': { title: 'Views', body: 'Views text.' }, 'list.search': { title: 'Search', body: 'Search text.' },
    'row.status': { title: 'Status', body: 'Status text.' },
  }
  const wait = () => new Promise((r) => setTimeout(r, 350))
  it('a stop seen on one list is not new on the next; a page with one more stop says "1 new" and plays only it', async () => {
    const { HelpProvider, PageHelp, TourManager, TourScope, TourMark, seenStore } = await import('./index')
    boxes()
    seenStore.mark([{ id: 'list.views' }, { id: 'list.search' }])  // toured list A
    render(<HelpProvider layers={[texts]}><TourManager>
      <TourScope id="b"><PageHelp id="page:b" page="B" />
        <TourMark help="list.views" slot="list.views">v</TourMark><TourMark help="list.search" slot="list.search">s</TourMark>
        <TourMark help="status:Sent" slot="row.status" tourKey="status">Sent</TourMark>
      </TourScope>
    </TourManager></HelpProvider>)
    await wait()
    const hint = await screen.findByRole('button', { name: '1 new on this page · Show' })
    fireEvent.click(hint)
    await waitFor(() => expect(document.querySelector('[data-tour-step]')?.textContent).toContain('Status text.'))
    expect(document.querySelector('[data-tour-step]')?.textContent).toContain('1 of 1')
    await waitFor(() => expect(seenStore.isSeen('b:row.status')).toBe(true))  // shown: seen, as this page's own
    expect(seenStore.isSeen('row.status')).toBe(false)  // not for every page
  })
  it('the same stops seen elsewhere: no hint at all; nothing seen ever: "New here? Show me"', async () => {
    const { HelpProvider, PageHelp, TourManager, TourScope, TourMark, seenStore } = await import('./index')
    boxes()
    const page = <HelpProvider layers={[texts]}><TourManager>
      <TourScope id="a"><PageHelp id="page:a" page="A" /><TourMark help="list.views" slot="list.views">v</TourMark></TourScope>
    </TourManager></HelpProvider>
    const { unmount } = render(page)
    await wait()
    expect(screen.getByRole('button', { name: 'New here? Show me' })).toBeInTheDocument()
    unmount()
    seenStore.mark([{ id: 'list.views' }])
    render(page)
    await wait()
    expect(document.querySelector('[data-help-new]')).toBeNull()
  })
  it('a raised version makes a seen stop new again', async () => {
    const { HelpProvider, PageHelp, TourManager, TourScope, TourMark, seenStore } = await import('./index')
    boxes()
    seenStore.mark([{ id: 'list.views' }, { id: 'list.search' }])
    render(<HelpProvider layers={[texts]}><TourManager slots={{ 'list.search': { v: 2 } }}>
      <TourScope id="a"><PageHelp id="page:a" page="A" />
        <TourMark help="list.views" slot="list.views">v</TourMark><TourMark help="list.search" slot="list.search">s</TourMark></TourScope>
    </TourManager></HelpProvider>)
    await wait()
    expect(await screen.findByRole('button', { name: '1 new on this page · Show' })).toBeInTheDocument()
  })
  it('the drawer: tours (all, what\'s new), the page\'s tips with New and Show, the catalog, replay and mark all', async () => {
    const { HelpProvider, PageHelp, TourManager, TourScope, TourMark, seenStore } = await import('./index')
    boxes()
    seenStore.mark([{ id: 'list.views' }])
    render(<HelpProvider layers={[texts]}><TourManager appName="Demo">
      <TourScope id="a"><PageHelp id="page:a" page="A" sections={[{ title: 'Using lists', ids: ['list.search'] }]} />
        <TourMark help="list.views" slot="list.views">v</TourMark><TourMark help="status:Sent" slot="row.status" tourKey="status">s</TourMark></TourScope>
    </TourManager></HelpProvider>)
    await wait()
    fireEvent.click(screen.getByRole('button', { name: 'Help: A' }))
    const panel = await screen.findByRole('dialog', { name: 'Help: A' })
    await waitFor(() => expect(within(panel).getByRole('group', { name: 'Tours' })).toBeInTheDocument())
    const tours = within(panel).getByRole('group', { name: 'Tours' })
    expect(tours.textContent).toContain('Show me around this page2')
    expect(tours.textContent).toContain("What's new here1")
    const statusRow = panel.querySelector('[data-help-row="row.status"]')!
    expect(statusRow.textContent).toContain('New')
    expect(within(statusRow as HTMLElement).getByRole('button', { name: 'Show Status' })).toBeInTheDocument()
    const searchRow = panel.querySelector('[data-help-row="list.search"]')!  // a tip with no place on the page: no Show
    expect(within(searchRow as HTMLElement).queryByRole('button')).toBeNull()
    fireEvent.click(within(panel).getByRole('button', { name: /^All Demo tips/ }))
    const cat = await within(panel).findByRole('region', { name: /^All Demo tips/ })
    expect(cat.textContent).toContain('Views')
    fireEvent.click(within(cat).getByRole('button', { name: 'Back' }))
    fireEvent.click(within(panel).getByRole('button', { name: 'Mark all as seen' }))
    expect(seenStore.isSeen('a:row.status')).toBe(true)
    fireEvent.click(within(panel).getByRole('button', { name: 'Replay all tips' }))
    expect(seenStore.isSeen('list.views')).toBe(false)
  })
})

describe('what is seen where', () => {
  afterEach(async () => { vi.restoreAllMocks(); (await import('./index')).seenStore.reset() })
  const boxes = () => vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
    return { width: 40, height: 20, top: 10, left: 10, right: 50, bottom: 30, x: 10, y: 10, toJSON: () => ({}) } as DOMRect
  })
  const texts = { 'page:c': { body: 'Page C.' }, 'list.views': { title: 'Views', body: 'Views text.' }, 'row.status': { title: 'Status', body: 'Status text.' },
    'list.sortGroup': { title: 'Sort and group', body: 'Sort text.' } }
  it("a page's own stop seen on one page is still new on another; a shared one is seen everywhere", async () => {
    const { HelpProvider, PageHelp, TourManager, TourScope, TourMark, seenStore } = await import('./index')
    boxes()
    seenStore.mark([{ id: 'list.views' }, { id: 'orders:row.status' }])  // Orders toured
    render(<HelpProvider layers={[texts]}><TourManager><TourScope id="c"><PageHelp id="page:c" page="C" />
      <TourMark help="list.views" slot="list.views">v</TourMark><TourMark help="status:X" slot="row.status" tourKey="status">s</TourMark>
    </TourScope></TourManager></HelpProvider>)
    await new Promise((r) => setTimeout(r, 350))
    expect(await screen.findByRole('button', { name: '1 new on this page · Show' })).toBeInTheDocument()
  })
  it('a tip with no place on the page counts as seen once it has been on screen in the drawer', async () => {
    const { HelpProvider, PageHelp, TourManager, TourScope, seenStore } = await import('./index')
    render(<HelpProvider layers={[texts]}><TourManager><TourScope id="c">
      <PageHelp id="page:c" page="C" sections={[{ title: 'Using lists', ids: ['list.sortGroup'] }]} />
    </TourScope></TourManager></HelpProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Help: C' }))
    await screen.findByRole('dialog', { name: 'Help: C' })
    await waitFor(() => expect(seenStore.isSeen('list.sortGroup')).toBe(true), { timeout: 2500 })
  })
})
