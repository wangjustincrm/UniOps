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
  it('the page panel shows a section of other texts (e.g. Using lists) under the page\'s own', async () => {
    const { PageHelp } = await import('./index')
    const texts = { 'page:x': { body: 'The page.' }, 'list.search': { title: 'Search', body: 'Type and press Enter.' } }
    render(<HelpProvider layers={[texts]}><PageHelp id="page:x" page="X" sections={[{ title: 'Using lists', ids: ['list.search', 'list.none'] }]} /></HelpProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Help: X' }))
    const sec = await screen.findByRole('region', { name: 'Using lists' })
    expect(within(sec).getByText('Search')).toBeInTheDocument()
    expect(within(sec).getByText('Type and press Enter.')).toBeInTheDocument()
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
