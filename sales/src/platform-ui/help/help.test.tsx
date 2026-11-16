import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
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
  it('a disabled button says why: focusable, by tap / click / Enter, and as its description', async () => {
    const { container } = render(<DisabledReason reason="Only a Draft can be submitted."><button disabled>Submit</button></DisabledReason>)
    const wrap = container.querySelector('[data-disabled-reason]') as HTMLElement
    expect(wrap).toHaveAccessibleDescription('Unavailable: Only a Draft can be submitted.')
    expect(wrap).toHaveAttribute('tabindex', '0')
    expect(wrap).toHaveAttribute('aria-disabled', 'true')
    fireEvent.keyDown(wrap, { key: 'Enter' })
    expect(within(await screen.findByRole('dialog')).getByText('Only a Draft can be submitted.')).toBeInTheDocument()
  })
  it('no reason: the control alone', () => {
    const { container } = render(<DisabledReason reason={null}><button>Go</button></DisabledReason>)
    expect(container.querySelector('[data-disabled-reason]')).toBeNull()
  })
})
