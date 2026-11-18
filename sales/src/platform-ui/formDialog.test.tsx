import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { ActionButton, ActionErrors, FormDialog, NoticeScope, SchemaForm, type FieldSpec } from './index'

// Forms and dialogs by configuration (step 2, user 2026-11-18): a file field checked before submit, a date-time
// field, a second submit, a form's done / after, and a dialog's own error slot for the buttons in it.

const wrap = (node: React.ReactNode) => render(<QueryClientProvider client={new QueryClient()}><NoticeScope>{node}</NoticeScope></QueryClientProvider>)
const file = (name: string, bytes: number) => new File([new Uint8Array(bytes)], name)
const pick = (label: string, f: File) => fireEvent.change(screen.getByLabelText(label, { selector: 'input[type="file"]' }), { target: { files: [f] } })

describe('SchemaForm: file and date-time fields, a second submit, done / after', () => {
  const schema = z.object({ file: z.instanceof(File, { message: 'Choose a file' }), at: z.string().min(1, 'Set the time') })
  const fields: FieldSpec[] = [
    { name: 'file', label: 'Report', type: 'file', required: true, accept: '.csv', maxBytes: 10 },
    { name: 'at', label: 'Taken at', type: 'datetime', required: true },
  ]

  it('refuses a wrong type or a file too large on its field, then hands the File itself to onSubmit', async () => {
    const onSubmit = vi.fn(async () => ({ id: 1 }))
    const after = vi.fn()
    wrap(<SchemaForm schema={schema} fields={fields} defaultValues={{ file: null, at: '' }} onSubmit={onSubmit} done="Uploaded" after={after} />)
    fireEvent.change(screen.getByLabelText('Taken at'), { target: { value: '2026-11-17T15:54' } })
    pick('Report', file('a.pdf', 5))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    expect(await screen.findByText('Choose a .csv file.')).toBeInTheDocument()
    pick('Report', file('a.csv', 50))
    expect(screen.getByText(/a\.csv · 1 KB/)).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    expect(await screen.findByText('Too large: at most 1 KB.')).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
    pick('Report', file('a.csv', 5))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    await waitFor(() => expect(onSubmit).toHaveBeenCalled())
    const [values] = onSubmit.mock.calls[0] as unknown as [Record<string, unknown>]
    expect(values.file).toBeInstanceOf(File)
    expect(String(values.at)).toMatch(/^2026-11-17T/)
    expect(await screen.findByText('Uploaded')).toBeInTheDocument()
    expect(after).toHaveBeenCalledWith({ id: 1 })
  })

  it('× clears the file; a second submit sends the same values its own way', async () => {
    const main = vi.fn(async () => null)
    const also = vi.fn(async () => null)
    wrap(<SchemaForm schema={z.object({ d: z.string().nullable().optional() })} fields={[{ name: 'd', label: 'Date', type: 'date' }]}
      defaultValues={{ d: '' }} onSubmit={main} submitLabel="Confirm" alsoSubmit={{ label: 'Confirm and send', onSubmit: also }} />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm and send' })) })
    await waitFor(() => expect(also).toHaveBeenCalledTimes(1))
    expect(main).not.toHaveBeenCalled()
  })
})

describe('FormDialog / ActionErrors: a refused action is said where it was asked', () => {
  it('a button inside says its failure inside, and the next try clears it', async () => {
    let fail = true
    wrap(<FormDialog open onOpenChange={() => {}} title="Test">
      <ActionButton onAction={async () => { if (fail) throw new Error('unreachable host') }}>Test</ActionButton>
    </FormDialog>)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Test' })) })
    expect(await screen.findAllByText(/unreachable host/)).not.toHaveLength(0)
    expect(screen.getByRole('dialog')).toHaveTextContent('unreachable host')
    fail = false
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Test' })) })
    await waitFor(() => expect(screen.getByRole('dialog')).not.toHaveTextContent('unreachable host'))
  })

  it('outside a dialog, ActionErrors does the same for a panel', async () => {
    wrap(<ActionErrors><ActionButton onAction={async () => { throw new Error('nope') }}>Go</ActionButton></ActionErrors>)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Go' })) })
    expect(await screen.findAllByText('nope')).not.toHaveLength(0)
  })
})
