import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TableCard } from './primitives/TableCard'

// Columns that do not fit (the user, 2026-11-04): the way to them sits under the
// rows, never over the header. jsdom has no layout, so the widths are set by hand.
const size = (el: HTMLElement, scrollWidth: number, clientWidth: number, scrollLeft = 0) => {
  Object.defineProperties(el, { scrollWidth: { value: scrollWidth, configurable: true }, clientWidth: { value: clientWidth, configurable: true },
    scrollLeft: { value: scrollLeft, configurable: true, writable: true } })
}
afterEach(() => { vi.restoreAllMocks() })

async function mount(scrollWidth: number, clientWidth: number) {
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 0)
  render(<TableCard><p>rows</p></TableCard>)
  const scroll = document.querySelector('[data-table-scroll]') as HTMLElement
  scroll.scrollBy = vi.fn()
  size(scroll, scrollWidth, clientWidth)
  fireEvent.scroll(scroll)
  return scroll
}

describe('the way to the columns that do not fit', () => {
  it('none when everything fits', async () => {
    await mount(300, 300)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('under the rows, outside what scrolls, so it covers nothing; it scrolls on', async () => {
    const scroll = await mount(900, 300)
    const more = await screen.findByRole('button', { name: 'More columns →' })
    expect(scroll.contains(more)).toBe(false)
    expect(more.closest('.absolute')).toBeNull()  // not floating over anything
    fireEvent.click(more)
    expect(scroll.scrollBy).toHaveBeenCalledWith({ left: 240, behavior: 'smooth' })
  })

  it('at the last column it takes you back, and stays in place', async () => {
    const scroll = await mount(900, 300)
    await screen.findByRole('button', { name: 'More columns →' })
    act(() => { size(scroll, 900, 300, 600); fireEvent.scroll(scroll) })
    await waitFor(() => expect(screen.getByRole('button', { name: '← First columns' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '← First columns' }))
    expect(scroll.scrollBy).toHaveBeenCalledWith({ left: -900, behavior: 'smooth' })
  })
})
