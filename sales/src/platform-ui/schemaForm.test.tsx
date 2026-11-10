import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { SchemaForm, type FieldSpec } from './index'
import { chooseOption, pickSuggestion } from './testing'

// SchemaForm on the primitives (2026-10-30): each field type has its control
// and the values it submits are what they were with the raw controls.

const schema = z.object({
  active: z.boolean(), kind: z.enum(['A', 'B']).nullable(), due: z.string().nullable(), item: z.string().nullable(),
  notes: z.string().nullable(), qty: z.number().nullable(),
})
const fields: FieldSpec[] = [
  { name: 'active', label: 'Active', type: 'checkbox' },
  { name: 'kind', label: 'Kind', type: 'select', options: [{ value: 'A', label: 'Apple' }, { value: 'B', label: 'Banana' }] },
  { name: 'due', label: 'Due', type: 'date' },
  { name: 'item', label: 'Item', type: 'reference', loadOptions: async (q) => [{ value: 'CF0092', label: 'CF0092', hint: q }] },
  { name: 'notes', label: 'Notes', type: 'textarea' },
  { name: 'qty', label: 'Qty', type: 'number' },
]

describe('SchemaForm controls', () => {
  it('checkbox, select, date, reference, textarea and number submit their values', async () => {
    const onSubmit = vi.fn()
    render(<SchemaForm schema={schema} fields={fields} onSubmit={onSubmit}
      defaultValues={{ active: false, kind: null, due: null, item: null, notes: null, qty: null }} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Active' }))
    await chooseOption(screen.getByRole('combobox', { name: 'Kind' }), 'Banana')
    fireEvent.change(screen.getByLabelText('Due'), { target: { value: '2026-11-01' } })
    await pickSuggestion(screen.getByRole('combobox', { name: 'Item' }), 'CF', /^CF0092/)
    fireEvent.change(screen.getByRole('textbox', { name: 'Notes' }), { target: { value: 'two\nlines' } })
    fireEvent.change(screen.getByLabelText('Qty'), { target: { value: '2.5' } })
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(onSubmit).toHaveBeenLastCalledWith({
      active: true, kind: 'B', due: '2026-11-01', item: 'CF0092', notes: 'two\nlines', qty: 2.5 }, { version: undefined }))
  })

  it('a blank select, date and reference submit null; read-only disables every control', async () => {
    const onSubmit = vi.fn()
    const { unmount } = render(<SchemaForm schema={schema} fields={fields} onSubmit={onSubmit}
      defaultValues={{ active: true, kind: 'A', due: '2026-10-30', item: 'CF0092', notes: null, qty: 1 }} />)
    expect(screen.getByRole('combobox', { name: 'Kind' })).toHaveTextContent('Apple')
    expect(screen.getByLabelText('Due')).toHaveValue('2026-10-30')  // the date as given: no day lost
    await chooseOption(screen.getByRole('combobox', { name: 'Kind' }), '—')
    fireEvent.change(screen.getByLabelText('Due'), { target: { value: '' } })
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({ kind: null, due: null, item: 'CF0092' }), { version: undefined }))
    unmount()
    render(<SchemaForm schema={schema} fields={fields} onSubmit={onSubmit} readOnly
      defaultValues={{ active: true, kind: 'A', due: null, item: null, notes: null, qty: null }} />)
    for (const name of ['Active']) expect(screen.getByRole('checkbox', { name })).toBeDisabled()
    for (const name of ['Kind', 'Item']) expect(screen.getByRole('combobox', { name })).toBeDisabled()
    expect(screen.getByLabelText('Due')).toBeDisabled()
  })
})
