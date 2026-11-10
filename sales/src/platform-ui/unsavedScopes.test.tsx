import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AskHost, Dialog, RecordSheet } from './index'
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

describe('the app dialog for unsaved input (2026-11-10)', () => {
  afterEach(() => vi.restoreAllMocks())
  const Dirty = ({ label }: { label: string }) => { useReportUnsaved(true); return <p>{label}</p> }

  it('asks in the app dialog when <AskHost> is mounted, and closes on Discard', async () => {
        const closed = vi.fn()
    const native = vi.spyOn(window, 'confirm')
    render(<><AskHost /><Dialog open onOpenChange={closed} title="Edit"><Dirty label="typed" /></Dialog></>)
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Edit' }), { key: 'Escape' })
    const question = await screen.findByRole('dialog', { name: 'Discard your changes?' })
    expect(native).not.toHaveBeenCalled()
    fireEvent.click(within(question).getByRole('button', { name: 'Keep editing' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Discard your changes?' })).toBeNull())
    expect(closed).not.toHaveBeenCalled()
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Edit' }), { key: 'Escape' })
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Discard your changes?' })).getByRole('button', { name: 'Discard' }))
    await waitFor(() => expect(closed).toHaveBeenCalledWith(false))
  })
})
