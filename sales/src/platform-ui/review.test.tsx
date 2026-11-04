// Review 2026-10-28: resolver shape, form preprocessing, double-fire guards,
// DataTable search / filters / page size, API error parsing.
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { useState } from 'react'
import { ActionButton, ConfirmAction, PermissionProvider, SchemaForm, type ColumnDef, type QuerySpec } from './index'
import { QueryTable } from './patterns/QueryTable'
import { clampPageSize, clearFilter, getFilter, setFilter } from './resource/querySpec'
import { zodResolver } from './patterns/zodResolver'
import { chooseOption } from './testing'

describe('zodResolver', () => {
  it('nests errors by path and puts object-level issues under root', async () => {
    const schema = z.object({ address: z.object({ city: z.string().min(1, 'City?') }), a: z.string(), b: z.string() })
      .refine((v) => v.a === v.b, { message: 'a and b must match' })
    const r = await zodResolver(schema)({ address: { city: '' }, a: 'x', b: 'x' }, undefined, {} as never)
    expect(r.errors).toEqual({ address: { city: { type: 'too_small', message: 'City?' } } })
    const r2 = await zodResolver(schema)({ address: { city: 'Toronto' }, a: 'x', b: 'y' }, undefined, {} as never)
    expect(r2.errors).toEqual({ root: { type: 'custom', message: 'a and b must match' } })
    expect(Object.keys(r2.errors)).not.toContain('address.city')
  })
})

describe('SchemaForm (review)', () => {
  it('an object-level refine blocks the submit and is shown', async () => {
    const schema = z.object({ low: z.number().nullable(), high: z.number().nullable() })
      .refine((v) => v.low == null || v.high == null || v.low <= v.high, { message: 'Low must not exceed high' })
    const fields = [{ name: 'low', label: 'Low', type: 'number' as const }, { name: 'high', label: 'High', type: 'number' as const }]
    const onSubmit = vi.fn()
    render(<SchemaForm schema={schema} fields={fields} defaultValues={{ low: 5, high: 1 }} onSubmit={onSubmit} />)
    fireEvent.click(screen.getByText('Save'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Low must not exceed high')
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('a cleared optional number is null (not 0), a typed one a number; a blank optional select passes', async () => {
    const schema = z.object({ qty: z.number().nullable().optional(), kind: z.enum(['A', 'B']).nullable().optional() })
    const fields = [
      { name: 'qty', label: 'Qty', type: 'number' as const },
      { name: 'kind', label: 'Kind', type: 'select' as const, options: [{ value: 'A', label: 'A' }, { value: 'B', label: 'B' }] },
    ]
    const onSubmit = vi.fn()
    render(<SchemaForm schema={schema} fields={fields} defaultValues={{ qty: 3, kind: null }} onSubmit={onSubmit} />)
    fireEvent.change(screen.getByLabelText('Qty'), { target: { value: '' } })
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(onSubmit).toHaveBeenLastCalledWith({ qty: null, kind: null }))
    fireEvent.change(screen.getByLabelText('Qty'), { target: { value: '12' } })
    await chooseOption(screen.getByLabelText('Kind'), 'B')
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(onSubmit).toHaveBeenLastCalledWith({ qty: 12, kind: 'B' }))
  })
})

describe('double-fire guards', () => {
  it('ActionButton is type=button and never submits a surrounding form', () => {
    const onSubmit = vi.fn((e: { preventDefault: () => void }) => e.preventDefault())
    render(
      <PermissionProvider can={() => true}>
        <form onSubmit={onSubmit}><ActionButton onAction={() => {}}>Act</ActionButton></form>
      </PermissionProvider>,
    )
    const btn = screen.getByText('Act').closest('button')!
    expect(btn).toHaveAttribute('type', 'button')
    fireEvent.click(btn)
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('two clicks in the same tick run the action once (ActionButton and Confirm)', async () => {
    let release!: () => void
    const onAction = vi.fn(() => new Promise<void>((r) => { release = r }))
    render(<PermissionProvider can={() => true}><ActionButton onAction={onAction}>Go</ActionButton></PermissionProvider>)
    const btn = screen.getByText('Go').closest('button')!
    act(() => { btn.click(); btn.click() }) // one batch: no re-render between the clicks
    expect(onAction).toHaveBeenCalledTimes(1)
    await act(async () => { release() })

    let done!: () => void
    const onConfirm = vi.fn(() => new Promise<void>((r) => { done = r }))
    render(<ConfirmAction open onOpenChange={() => {}} title="Sure?" confirmLabel="Yes" onConfirm={onConfirm} />)
    const yes = screen.getByText('Yes').closest('button')!
    act(() => { yes.click(); yes.click() })
    expect(onConfirm).toHaveBeenCalledTimes(1)
    await act(async () => { done() })
  })
})

interface Row { id: number; code: string }
const columns: ColumnDef<Row, unknown>[] = [{ id: 'code', header: 'Code', accessorKey: 'code' }]

function Table({ initial, onSpec }: { initial: QuerySpec; onSpec?: (s: QuerySpec) => void }) {
  const [spec, setSpec] = useState(initial)
  return (
    <>
      <button type="button" onClick={() => setSpec({ ...spec, search: 'from outside' })}>external</button>
      <QueryTable<Row>
        columns={columns} rows={[{ id: 1, code: 'A' }]} total={500} spec={spec} searchable rowKey={(r) => r.id}
        onSpecChange={(s) => { setSpec(s); onSpec?.(s) }}
      />
    </>
  )
}

describe('QueryTable (review)', () => {
  it('the search box is controlled: it follows a spec changed from outside', () => {
    render(<Table initial={{ page: 1, pageSize: 20, search: 'abc' }} />)
    expect(screen.getByLabelText('Search')).toHaveValue('abc')
    fireEvent.click(screen.getByText('external'))
    expect(screen.getByLabelText('Search')).toHaveValue('from outside')
  })

  // The per-page filter controls were replaced by the search panel (listView.test.tsx).

  it('a page size above the server maximum is clamped', () => {
    const onSpec = vi.fn()
    render(<Table initial={{ page: 1, pageSize: 1000 }} onSpec={onSpec} />)
    expect(screen.getByText(/1–200 of 500/)).toBeInTheDocument()
    expect(clampPageSize(1000)).toBe(200)
    expect(clampPageSize(0)).toBe(1)
  })
})

describe('filter adapter', () => {
  it('one filter per field; empty removes it; every change goes to page 1', () => {
    let s: QuerySpec = { page: 4, pageSize: 50 }
    s = setFilter(s, 'code', 'in', ['A', 'B'])
    s = setFilter(s, 'code', 'eq', 'C')
    expect(s.filters).toEqual([{ field: 'code', op: 'eq', value: 'C' }])
    expect(s.page).toBe(1)
    expect(getFilter(s, 'code')?.value).toBe('C')
    expect(clearFilter(s, 'code').filters).toEqual([])
    expect(setFilter(s, 'price', 'range', [null, null]).filters).toHaveLength(1)
  })
})
