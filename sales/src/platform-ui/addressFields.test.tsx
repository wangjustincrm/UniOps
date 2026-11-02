import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { z } from 'zod'
import { MasterDetailForm } from './patterns/MasterDetailForm'
import { addressSchema } from './patterns/address'
import { chooseOption } from './testing'

// AddressFields, as MasterDetailForm renders it (`addresses`), with addressSchema().

describe('AddressFields in MasterDetailForm', () => {
  const addrSchema = z.object({ ship_to: addressSchema() })
  const blankAddr = { country: 'CA', name: '', attention: '', phone: '', address1: '', address2: '', city: '', province: '', zip: '' }
  function AddrForm({ onSubmit }: { onSubmit: (v: Record<string, unknown>) => unknown }) {
    return <MasterDetailForm<{ item: string }> schema={addrSchema} fields={[]} defaultValues={{ ship_to: blankAddr }}
      addresses={[{ name: 'ship_to', legend: 'Ship to' }]} lines={{ label: 'Lines', columns: [], initial: [{ item: 'x' }] }}
      onSubmit={(v) => onSubmit(v)} />
  }
  const fill = (label: RegExp, value: string) => fireEvent.change(within(screen.getByRole('group', { name: 'Ship to' })).getByLabelText(label), { target: { value } })

  it('name, address, city, province and postal code are required; the postal code is A1A 1A1 in Canada', async () => {
    const onSubmit = vi.fn()
    render(<AddrForm onSubmit={onSubmit} />)
    const st = screen.getByRole('group', { name: 'Ship to' })
    expect(within(st).getByRole('combobox', { name: /^Country/ })).toHaveTextContent('Canada')  // the default
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await within(st).findAllByText('Required')).toHaveLength(5)
    fill(/^Name/, 'Jane'); fill(/^Address\b(?! line)/, '1 Main'); fill(/^City/, 'Ottawa'); fill(/^Postal code/, '12345')
    await chooseOption(within(st).getByRole('combobox', { name: /^Province/ }), 'Ontario (ON)')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await within(st).findByText('A Canadian postal code: A1A 1A1')).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
    fill(/^Postal code/, 'k1a 0b1')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit.mock.calls[0][0].ship_to).toMatchObject({ name: 'Jane', province: 'ON', zip: 'k1a 0b1', country: 'CA' })
  })

  it('outside Canada the province is free text and any postal code goes', async () => {
    const onSubmit = vi.fn()
    render(<AddrForm onSubmit={onSubmit} />)
    const st = screen.getByRole('group', { name: 'Ship to' })
    await chooseOption(within(st).getByRole('combobox', { name: /^Country/ }), 'United States')
    expect(within(st).queryByRole('combobox', { name: /^Province/ })).toBeNull()
    fill(/^Name/, 'Joe'); fill(/^Address\b(?! line)/, '1 Elm'); fill(/^City/, 'Buffalo'); fill(/^Province/, 'NY'); fill(/^Postal code/, '14201')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  })

  it("the server's error on ship_to.zip goes next to the postal code", async () => {
    const err = Object.assign(new Error('bad'), { body: { error: { code: 'validation', message: 'Invalid',
      details: { errors: [{ field: 'ship_to.zip', message: 'Not a postal code the warehouse takes' }] } } } })
    render(<AddrForm onSubmit={async () => { throw err }} />)
    const st = screen.getByRole('group', { name: 'Ship to' })
    fill(/^Name/, 'Jane'); fill(/^Address\b(?! line)/, '1 Main'); fill(/^City/, 'Ottawa'); fill(/^Postal code/, 'K1A 0B1')
    await chooseOption(within(st).getByRole('combobox', { name: /^Province/ }), 'Ontario (ON)')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await within(st).findByText('Not a postal code the warehouse takes')).toBeInTheDocument()
  })
})
