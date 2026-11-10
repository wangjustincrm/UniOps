import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RecordSheet } from './index'
import { useReportUnsaved } from './primitives/unsaved'

// Unsaved input per place (2026-11-10): a record sheet's tab and the sheet around it.
describe('RecordSheet and unsaved input', () => {
  afterEach(() => vi.restoreAllMocks())
  const Dirty = ({ label }: { label: string }) => { useReportUnsaved(true); return <p>{label}</p> }

  it('a record sheet asks before leaving a tab with unsaved input, and the sheet sees it too', async () => {
    const closed = vi.fn()
    render(<RecordSheet open onOpenChange={closed} title="R-1" tabs={[
      { id: 'edit', label: 'Edit', render: () => <Dirty label="form" /> },
      { id: 'other', label: 'Other', render: () => <p>other tab</p> },
    ]} />)
    const ask = vi.spyOn(window, 'confirm').mockReturnValueOnce(false)
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Other' }))
    expect(ask).toHaveBeenCalledTimes(1)
    expect(screen.getByText('form')).toBeInTheDocument()  // stayed
    ask.mockReturnValueOnce(false)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(closed).not.toHaveBeenCalled()                   // the sheet asks about the tab's form
    ask.mockReturnValueOnce(true)
    await new Promise((r) => setTimeout(r))  // a later click (one click asks once)
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Other' }))
    expect(await screen.findByText('other tab')).toBeInTheDocument()
  })
})
