import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Sidebar } from './chrome'

const brand = { name: 'UniOps' }

describe('Sidebar', () => {
  it('external items take their active state from `active`', () => {
    render(<MemoryRouter><Sidebar brand={brand} sections={[{ items: [
      { label: 'Home', href: 'https://p/', external: true, sameTab: true, active: true },
      { label: 'EPMS', href: 'https://e/', external: true, sameTab: true },
    ] }]} /></MemoryRouter>)
    expect(screen.getByRole('link', { name: 'Home' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'EPMS' })).not.toHaveAttribute('aria-current')
    // sameTab: no new-tab target
    expect(screen.getByRole('link', { name: 'Home' })).not.toHaveAttribute('target')
  })

  it('a collapsible group opens by itself when it holds the active item, and toggles', () => {
    const icon = <svg />
    const { rerender } = render(<MemoryRouter><Sidebar brand={brand} sections={[
      { title: 'MDM', collapsible: { icon }, items: [{ label: 'Staff', href: 'https://p/staff', external: true, sameTab: true, active: true }] },
    ]} /></MemoryRouter>)
    const toggle = screen.getByRole('button', { name: 'MDM' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: 'Staff' })).toBeInTheDocument()
    fireEvent.click(toggle)
    expect(screen.queryByRole('link', { name: 'Staff' })).toBeNull()
    // Icon-only sidebar: no group row, items listed directly.
    rerender(<MemoryRouter><Sidebar brand={brand} collapsed sections={[
      { title: 'MDM', collapsible: { icon }, items: [{ label: 'Staff', href: 'https://p/staff', external: true, sameTab: true }] },
    ]} /></MemoryRouter>)
    expect(screen.queryByRole('button', { name: 'MDM' })).toBeNull()
    expect(screen.getByTitle('Staff')).toBeInTheDocument()
  })
})
