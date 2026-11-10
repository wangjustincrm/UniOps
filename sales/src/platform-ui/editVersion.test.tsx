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
    const onSubmit = vi.fn()
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
})
