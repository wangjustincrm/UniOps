import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Callout } from './primitives/Callout'

// A callout may be closed (the user, 2026-11-27): `dismissible` for this visit, `once` for good in this browser;
// without storage it still works (closed for the visit). Default: it stays.

afterEach(() => { localStorage.clear(); vi.restoreAllMocks() })

describe('a callout', () => {
  it('stays by default: no ✕', () => {
    render(<Callout label="Note">Read me</Callout>)
    expect(screen.queryByRole('button', { name: /^Close/ })).toBeNull()
  })

  it('dismissible: ✕ closes it for this visit; shown again next time', () => {
    const { unmount } = render(<Callout label="Note" dismissible>Read me</Callout>)
    fireEvent.click(screen.getByRole('button', { name: 'Close Note' }))
    expect(screen.queryByText('Read me')).toBeNull()
    unmount()
    render(<Callout label="Note" dismissible>Read me</Callout>)
    expect(screen.getByText('Read me')).toBeInTheDocument()
  })

  it('once: closed, not shown again for this person; another callout is not affected', () => {
    const { unmount } = render(<Callout label="Note" once="tip-a">Read me</Callout>)
    fireEvent.click(screen.getByRole('button', { name: 'Close Note' }))
    unmount()
    render(<><Callout label="Note" once="tip-a">Read me</Callout><Callout label="Other" once="tip-b">Other text</Callout></>)
    expect(screen.queryByText('Read me')).toBeNull()
    expect(screen.getByText('Other text')).toBeInTheDocument()
  })

  it('without storage (a private window): shown, and closes for the visit', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    render(<Callout label="Note" once="tip-c">Read me</Callout>)
    expect(screen.getByText('Read me')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Close Note' }))
    expect(screen.queryByText('Read me')).toBeNull()
  })
})
