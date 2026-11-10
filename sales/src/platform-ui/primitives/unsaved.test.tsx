import { fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { confirmDiscard, useReportUnsaved } from './unsaved'

describe('unsaved changes', () => {
  afterEach(() => vi.restoreAllMocks())

  it('asks nothing when nothing is unsaved', () => {
    const ask = vi.spyOn(window, 'confirm')
    expect(confirmDiscard()).toBe(true)
    expect(ask).not.toHaveBeenCalled()
  })

  it('keeps the form when the person says no, drops it when they say yes', () => {
    const { unmount } = renderHook(() => useReportUnsaved(true))
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false)
    expect(confirmDiscard()).toBe(false)
    vi.spyOn(window, 'confirm').mockReturnValueOnce(true)
    expect(confirmDiscard()).toBe(true)
    unmount()
  })

  it('a form that closes no longer counts', () => {
    const { unmount } = renderHook(() => useReportUnsaved(true))
    unmount()
    const ask = vi.spyOn(window, 'confirm')
    expect(confirmDiscard()).toBe(true)
    expect(ask).not.toHaveBeenCalled()
  })
})

describe('unsaved changes, per place (2026-11-10)', () => {
  afterEach(() => vi.restoreAllMocks())
  const Dirty = ({ label }: { label: string }) => { useReportUnsaved(true); return <p>{label}</p> }

  it('closing a dialog asks only about its own forms, never a form behind it', async () => {
    const { Dialog } = await import('./Dialog')
    const closed = vi.fn()
    render(<><Dirty label="behind" /><Dialog open onOpenChange={closed} title="Confirm"><p>nothing typed</p></Dialog></>)
    const ask = vi.spyOn(window, 'confirm')
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(ask).not.toHaveBeenCalled()
    expect(closed).toHaveBeenCalledWith(false)
  })

  it('a dialog with a dirty form asks, and keeps it on No', async () => {
    const { Dialog } = await import('./Dialog')
    const closed = vi.fn()
    render(<Dialog open onOpenChange={closed} title="Edit"><Dirty label="typed" /></Dialog>)
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(closed).not.toHaveBeenCalled()
  })

})
