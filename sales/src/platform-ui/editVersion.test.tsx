import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MasterDetailForm, SchemaForm, textColumn, versionOf, type FieldSpec } from './index'

// Editing while someone else saves (2026-11-10): the version sent is the one the edit started from,
// a newer one is taken when nothing is typed, and typed input is never lost silently.
const schema = z.object({ name: z.string() })
const fields: FieldSpec[] = [{ name: 'name', label: 'Name' }]
const form = (version: number, name: string, onSubmit = vi.fn()) =>
  <SchemaForm schema={schema} fields={fields} version={version} defaultValues={{ name }} onSubmit={onSubmit} />

describe('SchemaForm with a version', () => {
  it('nothing typed: a newer version brings its values in', async () => {
    const { rerender } = render(form(1, 'Old'))
    rerender(form(2, 'New'))
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('New'))
    expect(screen.queryByText('Someone else saved this record meanwhile')).toBeNull()
  })

  it('typed: the input stays, it says so, the save sends the version it started from, Reload takes theirs', async () => {
    // As the server does: a save on an older version is refused (entity.stale), so the input stays typed.
    const onSubmit = vi.fn(async () => { throw Object.assign(new Error('stale'), { body: { error: { code: 'entity.stale', message: 'stale' } } }) })
    const { rerender } = render(form(1, 'Old', onSubmit))
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Mine' } })
    rerender(form(2, 'Theirs', onSubmit))
    expect(await screen.findByText('Someone else saved this record meanwhile')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('Mine')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ name: 'Mine' }, { version: 1 }))  // the server says stale
    fireEvent.click(screen.getByRole('button', { name: /Reload/ }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('Theirs'))
    expect(screen.queryByText('Someone else saved this record meanwhile')).toBeNull()
  })

  it('after its own save the form stands on what was saved and the new version: no "changed elsewhere"', async () => {
    const onSubmit = vi.fn(async () => ({ id: 1, name: 'Mine', version: 2 }))
    const { rerender } = render(form(1, 'Old', onSubmit))
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Mine' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ name: 'Mine' }, { version: 1 }))
    rerender(form(2, 'Mine', onSubmit))  // the record as the list now has it
    expect(screen.queryByText('Someone else saved this record meanwhile')).toBeNull()
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Again' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSubmit).toHaveBeenLastCalledWith({ name: 'Again' }, { version: 2 }))  // never the old version
  })
})

describe('after its own save, before the page has read the record again', () => {
  it.each(['typed again, then it comes', 'saved again, before it comes'])('%s: its own version, and nobody else is named', async (then) => {
    const sent: unknown[] = []
    const onSubmit = vi.fn(async (_v: unknown, edit: { version: number | undefined }) => { sent.push(edit.version); return { version: edit.version! + 1 } })
    const { rerender } = render(form(1, 'Old', onSubmit))
    const name = () => screen.getByRole('textbox', { name: 'Name' })
    fireEvent.change(name(), { target: { value: 'Mine' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(sent).toEqual([1]))
    rerender(form(1, 'Old', onSubmit))  // the page still has the record as it was
    await new Promise((r) => setTimeout(r, 30))
    expect(name()).toHaveValue('Mine')  // and does not bring it back
    fireEvent.change(name(), { target: { value: 'More' } })
    if (then.startsWith('saved')) {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      await waitFor(() => expect(sent).toEqual([1, 2]))
      return
    }
    rerender(form(2, 'Mine', onSubmit))
    await new Promise((r) => setTimeout(r, 30))
    expect(screen.queryByText('Someone else saved this record meanwhile')).toBeNull()
    expect(name()).toHaveValue('More')
    rerender(form(3, 'Theirs', onSubmit))  // and now someone else
    expect(await screen.findByText('Someone else saved this record meanwhile')).toBeInTheDocument()
  })
})

// A refused save (entity.stale): the record is read again as for an action, what was typed stays, and the form says what changed.
const STALE = Object.assign(new Error('stale'), { body: { error: { code: 'entity.stale', message: 'this record was changed by someone else; reload it', details: {} } } })
type L = { item: string }
const lineForm = (version: number, name: string, items: string[], onSubmit: () => Promise<unknown>, onChange = vi.fn()) => (
  <MasterDetailForm<L> schema={schema} fields={fields} version={version} defaultValues={{ name }} onChange={onChange} onSubmit={onSubmit}
    lines={{ label: 'Lines', initial: items.map((item) => ({ item })),
      columns: [textColumn<L>('item', 'Item', { label: (_, i) => `Item ${i + 1}`, get: (l) => l.item, set: (v) => ({ item: v }) })] }} />)
const within = (qc: QueryClient, ui: React.ReactNode) => <QueryClientProvider client={qc}>{ui}</QueryClientProvider>

describe('a save refused as stale', () => {
  it.each(['SchemaForm', 'MasterDetailForm'])('%s: reads again, keeps what was typed, and says what changed', async (kind) => {
    const qc = new QueryClient()
    const read = vi.spyOn(qc, 'invalidateQueries')
    const sent: unknown[] = []
    const refuse = async (...a: unknown[]) => { sent.push(a.at(-1)); throw STALE }
    const ui = (v: number, name: string, item: string) => within(qc, kind === 'SchemaForm'
      ? <SchemaForm schema={schema} fields={fields} version={v} defaultValues={{ name }} onSubmit={refuse} /> : lineForm(v, name, [item], refuse))
    const { rerender } = render(ui(1, 'Old', 'A'))
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Mine' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(/changed by someone else/)).toBeInTheDocument()
    expect(read).toHaveBeenCalledTimes(1)
    rerender(ui(2, 'Theirs', 'A'))
    expect(await screen.findByText('Changed: Name.')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('Mine')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(sent).toEqual([{ version: 1 }, { version: 1 }]))  // never the new one with the old content
  })

  it('another refusal reads nothing again', async () => {
    const qc = new QueryClient()
    const read = vi.spyOn(qc, 'invalidateQueries')
    render(within(qc, lineForm(1, 'Old', ['A'], async () => { throw Object.assign(new Error('no'), { body: { error: { code: 'x.no', message: 'not this' } } }) })))
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Mine' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('not this')).toBeInTheDocument()
    expect(read).not.toHaveBeenCalled()
  })

  it('MasterDetailForm: changed lines are named, and Reload takes theirs and tells the parent', async () => {
    const onChange = vi.fn()
    const ui = (v: number, item: string) => lineForm(v, 'Old', [item], async () => undefined, onChange)
    const { rerender } = render(ui(1, 'A'))
    fireEvent.change(screen.getByLabelText('Item 1'), { target: { value: 'MINE' } })
    rerender(ui(2, 'THEIRS'))
    expect(await screen.findByText('Changed: Lines.')).toBeInTheDocument()
    expect(screen.getByLabelText('Item 1')).toHaveValue('MINE')
    onChange.mockClear()
    fireEvent.click(screen.getByRole('button', { name: /Reload/ }))
    await waitFor(() => expect(screen.getByLabelText('Item 1')).toHaveValue('THEIRS'))
    expect(onChange).toHaveBeenLastCalledWith({ values: { name: 'Old' }, lines: [{ item: 'THEIRS' }] })
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'New name' } })
    expect(onChange.mock.calls.at(-1)![0].lines).toEqual([{ item: 'THEIRS' }])  // a header change reports the lines as they are now
  })

  it('a form that edits a record and was given no version says so', () => {
    expect(versionOf({ version: 3 })).toBe(3)
    expect(() => versionOf({ version: undefined })).toThrow('needs its version')
  })
})
