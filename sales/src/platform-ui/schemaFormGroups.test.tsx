import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { DayChips, Segmented } from './primitives/ChoiceControls'
import { SchemaForm, type FieldSpec } from './index'

// SchemaForm's groups and conditions (2026-11-04): titled sections, a collapsed
// Advanced part, fields that apply only to some values.

const schema = z.object({
  kind: z.enum(['A', 'B']),
  name: z.string().trim().min(1, 'Required'),
  folder: z.string().trim().min(1, 'Required for A'),
  days: z.array(z.string()).transform((d) => d.join(',')),
  extra: z.string().trim().optional(),
})
const fields: FieldSpec[] = [
  { name: 'kind', label: 'Kind', type: 'segmented', section: 'Basics', options: [{ value: 'A', label: 'Type A' }, { value: 'B', label: 'Type B' }] },
  { name: 'name', label: 'Name', section: 'Basics' },
  { name: 'folder', label: 'Folder', section: 'Files', showWhen: (v) => v.kind === 'A' },
  { name: 'extra', label: 'Extra', section: 'Basics', advanced: true },
  { name: 'days', label: 'Days', type: 'days', section: 'When' },
]
const mount = (over: Record<string, unknown> = {}, props: Partial<Parameters<typeof SchemaForm>[0]> = {}) => {
  const onSubmit = vi.fn()
  render(<SchemaForm schema={schema} fields={fields} onSubmit={onSubmit}
    defaultValues={{ kind: 'A', name: 'n', folder: '', days: [], extra: '', ...over }} {...props} />)
  return onSubmit
}
const save = () => fireEvent.click(screen.getByRole('button', { name: 'Save' }))

describe('sections', () => {
  it('titled groups in the order they first appear; a group with nothing to show is left out', () => {
    mount()
    expect(screen.getAllByRole('heading').map((h) => h.textContent)).toEqual(['Basics', 'Files', 'When'])
    fireEvent.click(screen.getByRole('radio', { name: 'Type B' }))
    expect(screen.getAllByRole('heading').map((h) => h.textContent)).toEqual(['Basics', 'When'])
  })

  it('Advanced is collapsed, and opens by itself when a field in it has an error', async () => {
    const strict = z.object({ extra: z.string().min(1, 'Needed') })
    render(<SchemaForm schema={strict} fields={[{ name: 'extra', label: 'Extra', advanced: true }]} defaultValues={{ extra: '' }} onSubmit={vi.fn()} />)
    const details = screen.getByText('Advanced').closest('details')!
    expect(details.open).toBe(false)
    save()
    await screen.findByText('Needed')
    expect(details.open).toBe(true)
  })

  it('a form without sections stays flat', () => {
    render(<SchemaForm schema={z.object({ a: z.string() })} fields={[{ name: 'a', label: 'A' }]} defaultValues={{ a: '' }} onSubmit={vi.fn()} />)
    expect(screen.queryByRole('heading')).toBeNull()
  })
})

describe('showWhen', () => {
  it('a field that applies is shown and validated', async () => {
    const onSubmit = mount()
    expect(screen.getByLabelText('Folder')).toBeInTheDocument()
    save()
    expect(await screen.findByText('Required for A')).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('one that does not is neither shown nor validated nor sent, and the others keep their parsing', async () => {
    const onSubmit = mount({ kind: 'B', name: '  n  ', folder: '', days: ['Mon', 'Fri'] })
    expect(screen.queryByLabelText('Folder')).toBeNull()
    save()
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce())
    expect(onSubmit.mock.calls[0][0]).toEqual({ kind: 'B', name: 'n', days: 'Mon,Fri', extra: null })  // trimmed, transformed, no folder
  })

  it('a value that passes validation is dropped too when it does not apply', async () => {
    const onSubmit = mount({ kind: 'B', folder: '/in' })
    save()
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce())
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('folder')
  })

  it('what was typed comes back when it applies again', () => {
    mount()
    fireEvent.change(screen.getByLabelText('Folder'), { target: { value: '/in' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Type B' }))
    fireEvent.click(screen.getByRole('radio', { name: 'Type A' }))
    expect(screen.getByLabelText('Folder')).toHaveValue('/in')
  })

  it('with changesOnly a changed field that no longer applies is not sent', async () => {
    const onSubmit = mount({ folder: '/in' }, { changesOnly: true })
    fireEvent.change(screen.getByLabelText('Folder'), { target: { value: '/out' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Type B' }))
    save()
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce())
    expect(onSubmit.mock.calls[0][0]).toEqual({ kind: 'B' })
  })
})

describe('the choice controls', () => {
  it('Segmented: a radio group, the arrows move and choose, one tab stop', () => {
    const onChange = vi.fn()
    render(<Segmented aria-label="Mode" value="T" onChange={onChange} options={[{ value: 'T', label: 'Test' }, { value: 'L', label: 'Live' }]} />)
    const group = screen.getByRole('radiogroup', { name: 'Mode' })
    expect(within(group).getByRole('radio', { name: 'Test' })).toBeChecked()
    expect(within(group).getByRole('radio', { name: 'Live' })).toHaveAttribute('tabindex', '-1')
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    expect(onChange).toHaveBeenCalledWith('L')
  })

  it('DayChips: toggles, and keeps the week\'s order whatever the clicks', () => {
    const onChange = vi.fn()
    render(<DayChips aria-label="Days" values={['Fri']} onChange={onChange} />)
    expect(screen.getByRole('button', { name: 'Fri' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Mon' }))
    expect(onChange).toHaveBeenCalledWith(['Mon', 'Fri'])
  })
})
