import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { z } from 'zod'
import { MasterDetailForm } from './patterns/MasterDetailForm'
import { POSTAL_PROVINCES, addressSchema, isPoBox, postalMismatch } from './patterns/address'
import { chooseOption } from './testing'

// AddressFields, as MasterDetailForm renders it (`addresses`), with addressSchema().

describe('AddressFields in MasterDetailForm', () => {
  const addrSchema = z.object({ ship_to: addressSchema() })
  const blankAddr = { country: 'CA', name: '', attention: '', phone: '', address1: '', address2: '', city: '', province: '', zip: '' }
  const withPhone = z.object({ ship_to: addressSchema({ phone: true }) })
  function AddrForm({ onSubmit, phone }: { onSubmit: (v: Record<string, unknown>) => unknown; phone?: boolean }) {
    return <MasterDetailForm<{ item: string }> schema={phone ? withPhone : addrSchema} fields={[]} defaultValues={{ ship_to: blankAddr }}
      addresses={[{ name: 'ship_to', legend: 'Ship to', phoneRequired: phone }]} lines={{ label: 'Lines', columns: [], initial: [{ item: 'x' }] }}
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

  async function fillValid(st: HTMLElement, zip = 'K1A 0B1') {
    fill(/^Name/, 'Jane'); fill(/^Address\b(?! line)/, '1 Main'); fill(/^City/, 'Ottawa'); fill(/^Postal code/, zip)
    await chooseOption(within(st).getByRole('combobox', { name: /^Province/ }), 'Ontario (ON)')
  }

  it('a postal code from another province is refused next to it; the right one goes', async () => {
    const onSubmit = vi.fn()
    render(<AddrForm onSubmit={onSubmit} />)
    const st = screen.getByRole('group', { name: 'Ship to' })
    await fillValid(st, 'H2X 1Y4')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await within(st).findByText('This postal code is in QC, not ON')).toBeInTheDocument()
    fill(/^Postal code/, 'K1A 0B1')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  })

  it('a PO box warns and needs a confirmation, never refused', async () => {
    const onSubmit = vi.fn()
    render(<AddrForm onSubmit={onSubmit} />)
    const st = screen.getByRole('group', { name: 'Ship to' })
    await fillValid(st)
    expect(within(st).queryByRole('note')).toBeNull()
    fill(/^Address line 2/, 'P.O. Box 12')
    expect(within(st).getByRole('note')).toHaveTextContent('carriers may not deliver there')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await within(st).findByText('Confirm the PO box address')).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.click(within(st).getByRole('checkbox', { name: 'Ship to this PO box anyway' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  })

  it('with phoneRequired the phone is required', async () => {
    const onSubmit = vi.fn()
    render(<AddrForm onSubmit={onSubmit} phone />)
    const st = screen.getByRole('group', { name: 'Ship to' })
    await fillValid(st)
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await within(st).findByText('Required')).toBeInTheDocument()
    expect(within(st).getByLabelText(/^Phone/)).toHaveAttribute('aria-invalid', 'true')
    fill(/^Phone/, '613-555-0100')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  })
})

describe('postal code ↔ province and PO box (the same table and pattern as the server)', () => {
  const expected: Record<string, string> = { A: 'NL', B: 'NS', C: 'PE', E: 'NB', G: 'QC', H: 'QC', J: 'QC', K: 'ON', L: 'ON', M: 'ON',
    N: 'ON', P: 'ON', R: 'MB', S: 'SK', T: 'AB', V: 'BC', X: 'NT', Y: 'YT' }
  it('every first letter belongs to its province only', () => {
    expect(Object.keys(POSTAL_PROVINCES).sort()).toEqual(Object.keys(expected).sort())
    for (const [letter, prov] of Object.entries(expected)) {
      expect(postalMismatch(prov, `${letter}1A 1A1`)).toBeNull()
      expect(postalMismatch(prov === 'BC' ? 'ON' : 'BC', `${letter}1A 1A1`)).toMatch(/^This postal code is in /)
    }
    expect(postalMismatch('NU', 'X0A 0H0')).toBeNull()
    expect(postalMismatch('YT', 'X0A 0H0')).toBe('This postal code is in NT/NU, not YT')
  })
  it('PO box variants', () => {
    for (const l of ['PO Box 12', 'P.O. Box 12', 'Post Office Box 3', 'CP 34', 'C.P. 34', 'Case postale 5', 'Box 56', 'BOX #9']) expect(isPoBox(l)).toBe(true)
    for (const l of ['1 Boxwood Cres', '12 Main St', 'CPR Station Rd', 'Postal Plaza']) expect(isPoBox(l)).toBe(false)
  })
})
