import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Autocomplete, DateInput, DateTimeInput, Textarea } from './index'
import { Checkbox, CheckboxList } from './primitives/Checkbox'
import { NumberInput } from './primitives/FieldInputs'
import { Select } from './primitives/Select'
import { Sheet } from './primitives/Sheet'
import { isoToLocalInput, localInputToIso } from './primitives/dateValues'
import { chooseOption, pickSuggestion } from './testing'

// The form primitives (2026-10-30): keyboard, accessible names and values.

function SelectHarness({ emptyLabel, inSheet }: { emptyLabel?: string; inSheet?: boolean }) {
  const [v, setV] = useState<string | null>('A')
  const body = (
    <>
      <Select aria-label="Kind" value={v} onChange={setV} emptyLabel={emptyLabel}
        options={[{ value: 'A', label: 'Apple' }, { value: 'B', label: 'Banana' }]} />
      <output>value={String(v)}</output>
    </>
  )
  return inSheet ? <Sheet open onOpenChange={() => {}} title="Form">{body}</Sheet> : body
}

describe('Select', () => {
  it('is a combobox named by its label, opens with the keyboard and picks an option', async () => {
    render(<SelectHarness />)
    const trigger = screen.getByRole('combobox', { name: 'Kind' })
    expect(trigger).toHaveTextContent('Apple')
    fireEvent.keyDown(trigger, { key: 'Enter' })
    const list = await screen.findByRole('listbox')
    expect(within(list).getAllByRole('option').map((o) => o.textContent)).toEqual(['Apple', 'Banana'])
    fireEvent.click(within(list).getByRole('option', { name: 'Banana' }))
    expect(screen.getByText('value=B')).toBeInTheDocument()
  })
  it('an empty option clears to null; works inside a sheet (one focus stack)', async () => {
    render(<SelectHarness emptyLabel="—" inSheet />)
    await chooseOption(screen.getByRole('combobox', { name: 'Kind' }), '—')
    expect(screen.getByText('value=null')).toBeInTheDocument()
    await chooseOption(screen.getByRole('combobox', { name: 'Kind' }), 'Banana')
    expect(screen.getByText('value=B')).toBeInTheDocument()
  })
})

describe('Checkbox / CheckboxList', () => {
  it('is a checkbox named by its label; Space and a click on the label toggle it', () => {
    const onChange = vi.fn()
    const { rerender } = render(<Checkbox label="Active" checked={false} onCheckedChange={onChange} />)
    const box = screen.getByRole('checkbox', { name: 'Active' })
    expect(box).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(screen.getByText('Active'))
    expect(onChange).toHaveBeenLastCalledWith(true)
    rerender(<Checkbox label="Active" checked onCheckedChange={onChange} />)
    expect(screen.getByRole('checkbox', { name: 'Active' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Active' }))
    expect(onChange).toHaveBeenLastCalledWith(false)
    rerender(<Checkbox label="Active" checked disabled onCheckedChange={onChange} />)
    expect(screen.getByRole('checkbox', { name: 'Active' })).toBeDisabled()
  })
  it('a list is a named group of checkboxes; ticking adds, unticking removes', () => {
    const onChange = vi.fn()
    render(<CheckboxList label="Status" values={['Review']} onChange={onChange}
      options={[{ value: 'Pending', label: 'Pending' }, { value: 'Review', label: 'Review' }]} />)
    const group = screen.getByRole('group', { name: 'Status' })
    fireEvent.click(within(group).getByRole('checkbox', { name: 'Pending' }))
    expect(onChange).toHaveBeenLastCalledWith(['Review', 'Pending'])
    fireEvent.click(within(group).getByRole('checkbox', { name: 'Review' }))
    expect(onChange).toHaveBeenLastCalledWith([])
  })
})

describe('inputs', () => {
  it('NumberInput keeps the typed text; DateInput a plain date string; Textarea is a named textbox', () => {
    const onNumber = vi.fn((e) => e.target.value)
    const onDate = vi.fn((e) => e.target.value)
    render(<><NumberInput aria-label="Qty" defaultValue="" onChange={onNumber} />
      <DateInput aria-label="Due" defaultValue="" onChange={onDate} /><Textarea aria-label="Notes" /></>)
    fireEvent.change(screen.getByLabelText('Qty'), { target: { value: '1.25' } })
    expect(onNumber).toHaveReturnedWith('1.25')
    expect(screen.getByLabelText('Qty')).toHaveAttribute('inputmode', 'decimal')
    fireEvent.change(screen.getByLabelText('Due'), { target: { value: '2026-10-30' } })
    expect(onDate).toHaveReturnedWith('2026-10-30')  // the string itself: no Date, no zone, no day lost
    expect(screen.getByRole('textbox', { name: 'Notes' })).toBeInTheDocument()
  })
  it('DateTimeInput shows local time and gives back ISO with the zone', () => {
    const iso = '2026-10-30T18:05:00.000Z'
    expect(localInputToIso(isoToLocalInput(iso))).toBe(iso)  // round trip in any zone
    const onChange = vi.fn()
    render(<DateTimeInput aria-label="Cursor" value={iso} onChange={onChange} />)
    expect(screen.getByLabelText('Cursor')).toHaveValue(isoToLocalInput(iso))
    fireEvent.change(screen.getByLabelText('Cursor'), { target: { value: '' } })
    expect(onChange).toHaveBeenLastCalledWith(null)
  })
})

describe('Autocomplete', () => {
  function Harness({ load, minChars }: { load: (q: string) => Promise<{ value: string; label: string }[]>; minChars?: number }) {
    const [v, setV] = useState<string | null>(null)
    return <><Autocomplete aria-label="Item" value={v} onChange={setV} loadOptions={load} minChars={minChars} debounceMs={50} /><output>picked={String(v)}</output></>
  }

  it('asks only after min chars and the pause, then picks with the keyboard', async () => {
    const load = vi.fn(async (q: string) => [{ value: `${q}-1`, label: `${q} one` }, { value: `${q}-2`, label: `${q} two` }])
    render(<Harness load={load} minChars={2} />)
    const trigger = screen.getByRole('combobox', { name: 'Item' })
    fireEvent.click(trigger)
    const search = await screen.findByRole('textbox', { name: 'Item: search' })
    fireEvent.change(search, { target: { value: 'C' } })
    expect(screen.getByText('Type at least 2 characters')).toBeInTheDocument()
    fireEvent.change(search, { target: { value: 'CF' } })
    await screen.findByText('CF one')
    expect(load).toHaveBeenCalledTimes(1)  // "C" was never sent
    fireEvent.keyDown(search, { key: 'ArrowDown' })
    fireEvent.keyDown(search, { key: 'Enter' })
    expect(screen.getByText('picked=CF-2')).toBeInTheDocument()
  })

  it('drops an answer to an older query and says when loading failed', async () => {
    let release: (v: { value: string; label: string }[]) => void = () => {}
    const load = vi.fn((q: string) => (q === 'ab'
      ? new Promise<{ value: string; label: string }[]>((r) => { release = r })
      : q === 'xx' ? Promise.reject(new Error('down')) : Promise.resolve([{ value: 'abc', label: 'abc' }])))
    render(<Harness load={load} />)
    fireEvent.click(screen.getByRole('combobox', { name: 'Item' }))
    const search = await screen.findByRole('textbox', { name: 'Item: search' })
    fireEvent.change(search, { target: { value: 'ab' } })
    await waitFor(() => expect(load).toHaveBeenCalledWith('ab'))
    fireEvent.change(search, { target: { value: 'abc' } })
    await screen.findByRole('option', { name: 'abc' })
    await act(async () => release([{ value: 'old', label: 'old answer' }]))
    expect(screen.queryByText('old answer')).toBeNull()
    fireEvent.change(search, { target: { value: 'xx' } })
    expect(await screen.findByText('Could not load the options')).toBeInTheDocument()
  })

  it('says Searching… until the answer for what is typed arrives (never an early No match)', async () => {
    render(<Harness load={(q) => new Promise((r) => setTimeout(() => r([{ value: q, label: `got ${q}` }]), 100))} />)
    fireEvent.click(screen.getByRole('combobox', { name: 'Item' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Item: search' }), { target: { value: 'zz' } })
    expect(screen.getByText('Searching…')).toBeInTheDocument()
    expect(screen.queryByText('No match')).toBeNull()
    expect(await screen.findByText('got zz')).toBeInTheDocument()
  })

  it('two in a row: the first closes and gives the focus back before the second opens', async () => {
    function Two() {
      const [a, setA] = useState<string | null>(null)
      const [b, setB] = useState<string | null>(null)
      return <>
        <Autocomplete aria-label="A" value={a} onChange={setA} minChars={0} loadOptions={async () => [{ value: 'MAN', label: 'MAN' }]} />
        <Autocomplete aria-label="B" value={b} onChange={setB} loadOptions={async (q) => [{ value: 'CF0092', label: `CF0092 ${q}` }]} />
        <output>{a}/{b}</output>
      </>
    }
    render(<Two />)
    await pickSuggestion(screen.getByRole('combobox', { name: 'A' }), 'M', /^MAN/)
    await pickSuggestion(screen.getByRole('combobox', { name: 'B' }), 'CF', /^CF0092/)
    expect(screen.getByText('MAN/CF0092')).toBeInTheDocument()
  })

  it('the test helper drives it like a user', async () => {
    render(<Harness load={async (q) => [{ value: 'CF0092', label: `CF0092 (${q})` }]} />)
    await pickSuggestion(screen.getByRole('combobox', { name: 'Item' }), 'CF', /^CF0092/)
    expect(screen.getByText('picked=CF0092')).toBeInTheDocument()
  })
})
