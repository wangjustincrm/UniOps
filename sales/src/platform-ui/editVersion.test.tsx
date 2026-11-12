import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { SchemaForm, type FieldSpec } from './index'

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
