import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { useState } from 'react'
import { ActionButton, ConfirmAction, PermissionProvider, SchemaForm, ToastProvider, type ColumnDef, type QuerySpec } from './index'
import { Combobox } from './primitives/Combobox'
import { QueryTable } from './patterns/QueryTable'
import { PermissionGate } from './patterns/PermissionGate'
import { StatusBadge } from './primitives/StatusBadge'
import { useToast } from './primitives/toastContext'

const allow = (keys: string[]) => (k: string) => keys.includes(k)

describe('StatusBadge', () => {
  it('renders the map label and falls back to the raw status', () => {
    const map = { 'On Hold': { variant: 'danger' as const, label: 'On hold' } }
    const { rerender } = render(<StatusBadge status="On Hold" map={map} />)
    expect(screen.getByText('On hold')).toBeInTheDocument()
    rerender(<StatusBadge status="Unknown" map={map} />)
    expect(screen.getByText('Unknown')).toBeInTheDocument()
    rerender(<StatusBadge status={null} map={map} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})

describe('PermissionGate / ActionButton', () => {
  it('shows content only with the permission (negative and positive)', () => {
    const { rerender } = render(
      <PermissionProvider can={allow([])}><PermissionGate permission="x.op" fallback={<span>no</span>}><span>yes</span></PermissionGate></PermissionProvider>,
    )
    expect(screen.getByText('no')).toBeInTheDocument()
    rerender(
      <PermissionProvider can={allow(['x.op'])}><PermissionGate permission="x.op" fallback={<span>no</span>}><span>yes</span></PermissionGate></PermissionProvider>,
    )
    expect(screen.getByText('yes')).toBeInTheDocument()
  })

  it('hides or disables without the permission', () => {
    const { rerender } = render(
      <PermissionProvider can={allow([])}><ActionButton permission="x.op" onAction={() => {}}>Go</ActionButton></PermissionProvider>,
    )
    expect(screen.queryByText('Go')).toBeNull()
    rerender(
      <PermissionProvider can={allow([])}><ActionButton permission="x.op" whenDenied="disable" onAction={() => {}}>Go</ActionButton></PermissionProvider>,
    )
    expect(screen.getByText('Go').closest('button')).toBeDisabled()
  })

  it('cannot fire twice while pending', async () => {
    let release!: () => void
    const onAction = vi.fn(() => new Promise<void>((r) => { release = r }))
    render(
      <PermissionProvider can={allow(['x.op'])}>
        <ActionButton permission="x.op" onAction={onAction} pendingLabel="Sending…">Send</ActionButton>
      </PermissionProvider>,
    )
    const btn = screen.getByText('Send').closest('button')!
    fireEvent.click(btn)
    fireEvent.click(btn)
    expect(onAction).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Sending…').closest('button')).toBeDisabled()
    await act(async () => { release() })
    expect(screen.getByText('Send').closest('button')).not.toBeDisabled()
  })
})

interface Thing { id: number; code: string }
const columns: ColumnDef<Thing, unknown>[] = [
  { id: 'code', header: 'Code', accessorKey: 'code' },
  { id: 'note', header: 'Note', cell: () => 'n/a' },
]

function Table({ onSpec, rows = [{ id: 1, code: 'A' }], total = 1, error }: {
  onSpec: (s: QuerySpec) => void; rows?: Thing[]; total?: number; error?: unknown
}) {
  const [spec, setSpec] = useState<QuerySpec>({ page: 1, pageSize: 20 })
  return (
    <QueryTable<Thing>
      columns={columns} rows={rows} total={total} spec={spec} sortable={['code']} searchable error={error}
      onSpecChange={(s) => { setSpec(s); onSpec(s) }} rowKey={(r) => r.id}
    />
  )
}

describe('QueryTable', () => {
  it('turns header clicks into spec.sort, only on sortable columns', () => {
    const onSpec = vi.fn()
    render(<Table onSpec={onSpec} />)
    fireEvent.click(screen.getByRole('button', { name: /Code/ }))
    expect(onSpec).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, sort: [{ field: 'code', desc: false }] }))
    fireEvent.click(screen.getByRole('button', { name: /Code/ }))
    expect(onSpec).toHaveBeenLastCalledWith(expect.objectContaining({ sort: [{ field: 'code', desc: true }] }))
    expect(screen.queryByRole('button', { name: /Note/ })).toBeNull()
  })

  it('search goes to spec.search on Enter and resets the page', () => {
    const onSpec = vi.fn()
    render(<Table onSpec={onSpec} />)
    const box = screen.getByLabelText('Search')
    fireEvent.change(box, { target: { value: 'abc' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSpec).toHaveBeenLastCalledWith(expect.objectContaining({ search: 'abc', page: 1 }))
  })

  it('shows empty and error states', () => {
    const { rerender } = render(<Table onSpec={() => {}} rows={[]} total={0} />)
    expect(screen.getByText('Nothing here yet')).toBeInTheDocument()
    rerender(<Table onSpec={() => {}} error={{ body: { error: { code: 'query.unknown_field', message: 'bad sort', details: {} } } }} />)
    expect(screen.getByRole('alert')).toHaveTextContent('bad sort')
  })
})

describe('error text', () => {
  it('an unreachable server reads as such, not as "Failed to fetch"', async () => {
    const { errorText } = await import('./resource/businessError')
    expect(errorText(new TypeError('Failed to fetch'))).toMatch(/could not be reached/)
    expect(errorText(new Error('boom'))).toBe('boom')  // other errors unchanged
  })
})

describe('SchemaForm', () => {
  const schema = z.object({ code: z.string().min(1, 'Required'), email: z.string().nullable().optional() })
  const fields = [{ name: 'code', label: 'Code', required: true }, { name: 'email', label: 'Email' }]

  it('puts each field the server refused next to its input (details.errors)', async () => {
    const onSubmit = vi.fn(async () => {
      throw { body: { error: { code: 'request.invalid', message: 'x', details: { field: 'code', errors: [
        { field: 'code', message: 'Too long' }, { field: 'email', message: 'Not an email' }, { field: 'other', message: 'ignored' },
      ] } } } }
    })
    render(<SchemaForm schema={schema} fields={fields} defaultValues={{ code: 'A', email: 'a' }} onSubmit={onSubmit} />)
    fireEvent.click(screen.getByText('Save'))
    expect(await screen.findByText('Too long')).toBeInTheDocument()
    expect(screen.getByText('Not an email')).toBeInTheDocument()
    expect(screen.queryByText('ignored')).not.toBeInTheDocument()
  })

  it('a numeric select takes and gives numbers (an id), null for none', async () => {  // 2026-11-10
    const onSubmit = vi.fn()
    const opts = [{ value: '7', label: 'Radiant' }, { value: '9', label: 'TrueCommerce' }]
    render(<SchemaForm schema={z.object({ a: z.number().nullable(), b: z.number().nullable() })} defaultValues={{ a: 7, b: null }}
      fields={[{ name: 'a', label: 'A', type: 'select', numeric: true, options: opts }, { name: 'b', label: 'B', type: 'select', numeric: true, options: opts }]}
      onSubmit={onSubmit} />)
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(onSubmit).toHaveBeenCalled())
    expect(onSubmit.mock.calls[0][0]).toEqual({ a: 7, b: null })
  })

  it('read-only: every input disabled and no save (the editable form has one)', () => {
    const { unmount } = render(<SchemaForm schema={schema} fields={fields} defaultValues={{ code: 'A' }} readOnly onCancel={() => {}} onSubmit={vi.fn()} />)
    expect(screen.getByLabelText(/Code/)).toBeDisabled()
    expect(screen.getByLabelText(/Email/)).toBeDisabled()
    expect(screen.queryByText('Save')).not.toBeInTheDocument()
    expect(screen.getByText('Close')).toBeInTheDocument()
    unmount()
    render(<SchemaForm schema={schema} fields={fields} defaultValues={{ code: 'A' }} onSubmit={vi.fn()} />)
    expect(screen.getByLabelText(/Code/)).toBeEnabled()
    expect(screen.getByText('Save')).toBeInTheDocument()
  })

  it('sends only changed fields, a cleared field as null', async () => {
    const onSubmit = vi.fn()
    render(<SchemaForm schema={schema} fields={fields} defaultValues={{ code: 'A', email: 'a@x.com' }} changesOnly onSubmit={onSubmit} />)
    fireEvent.change(screen.getByLabelText(/Email/), { target: { value: '' } })
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ email: null }, { version: undefined }))
  })

  it('submitting nothing changed sends an empty object', async () => {
    const onSubmit = vi.fn()
    render(<SchemaForm schema={schema} fields={fields} defaultValues={{ code: 'A', email: null }} changesOnly onSubmit={onSubmit} />)
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({}, { version: undefined }))
  })

  it('shows schema errors and server errors on the field they name', async () => {
    const onSubmit = vi.fn(async () => {
      throw { body: { error: { code: 'x.taken', message: 'Code taken', details: { field: 'code' } } } }
    })
    render(<SchemaForm schema={schema} fields={fields} defaultValues={{ code: '' }} onSubmit={onSubmit} />)
    fireEvent.click(screen.getByText('Save'))
    expect(await screen.findByText('Required')).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText(/Code/), { target: { value: 'A' } })
    fireEvent.click(screen.getByText('Save'))
    expect(await screen.findByText('Code taken')).toBeInTheDocument()
  })
})

describe('ConfirmAction', () => {
  it('stays open with the error when the action fails, closes on success', async () => {
    const onOpenChange = vi.fn()
    const failing = vi.fn(async () => { throw { body: { error: { code: 'c', message: 'Refused', details: {} } } } })
    const { rerender } = render(
      <ConfirmAction open onOpenChange={onOpenChange} title="Sure?" confirmLabel="Do it" onConfirm={failing} />,
    )
    fireEvent.click(screen.getByText('Do it'))
    expect(await screen.findByText('Refused')).toBeInTheDocument()
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
    rerender(<ConfirmAction open onOpenChange={onOpenChange} title="Sure?" confirmLabel="Do it" onConfirm={async () => {}} />)
    fireEvent.click(screen.getByText('Do it'))
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })
})

describe('Combobox', () => {
  it('filters and picks with the keyboard', async () => {
    const onChange = vi.fn()
    render(<Combobox options={[{ value: 'a', label: 'Apple' }, { value: 'b', label: 'Banana' }]} value={null} onChange={onChange} />)
    fireEvent.click(screen.getByRole('combobox'))
    const search = await screen.findByLabelText('Search options')
    fireEvent.change(search, { target: { value: 'ban' } })
    expect(screen.queryByText('Apple')).toBeNull()
    fireEvent.keyDown(search, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('b')
  })
})

describe('Toast', () => {
  it('shows a toast through the hook', async () => {
    function Shout() {
      const toast = useToast()
      return <button onClick={() => toast({ title: 'Saved', variant: 'success' })}>go</button>
    }
    render(<ToastProvider><Shout /></ToastProvider>)
    fireEvent.click(screen.getByText('go'))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
  })
})
