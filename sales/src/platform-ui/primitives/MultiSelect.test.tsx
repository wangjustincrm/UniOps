import { useState } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MultiChoice } from './MultiChoice'
import { MultiSelect } from './MultiSelect'
import { submitOnEnter } from './submitOnEnter'
import { Select } from './Select'
import { TagInput } from './TagInput'

const OPTIONS = ['To Fulfill', 'Sent', 'Fulfilling', 'Shipped', 'To Invoice', 'Completed', 'On Hold'].map((v) => ({ value: v, label: v }))

function Form({ onSearch }: { onSearch: (v: string[]) => void }) {
  const [values, setValues] = useState<string[]>([])
  return (
    <form onKeyDownCapture={submitOnEnter} onSubmit={(e) => { e.preventDefault(); onSearch(values) }}>
      <MultiSelect label="Status" options={OPTIONS} values={values} onChange={setValues} />
      <button type="button">Clear</button>
    </form>
  )
}

const box = () => screen.getByRole('combobox', { name: 'Status' })

describe('MultiSelect and the Enter rule', () => {
  it('Enter chooses while the list is open, then confirms and searches', async () => {
    const onSearch = vi.fn()
    render(<Form onSearch={onSearch} />)
    fireEvent.change(box(), { target: { value: 'ship' } })
    await screen.findByRole('option', { name: 'Shipped' })
    fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSearch).not.toHaveBeenCalled()  // the first Enter chooses
    expect(screen.getByText('Shipped')).toBeInTheDocument()  // now a tag in the box
    expect(box()).toHaveAttribute('aria-expanded', 'false')
    fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSearch).toHaveBeenCalledWith(['Shipped'])  // the second Enter searches
  })

  it('the box holds what is chosen and the list offers only the rest; a click leaves it open', async () => {
    render(<Form onSearch={vi.fn()} />)
    fireEvent.keyDown(box(), { key: 'ArrowDown' })
    const list = await screen.findByRole('listbox', { name: 'Status' })
    fireEvent.click(within(list).getByRole('option', { name: 'Sent' }))
    fireEvent.click(within(list).getByRole('option', { name: 'On Hold' }))
    expect(within(list).queryByRole('option', { name: 'Sent' })).toBeNull()
    expect(within(list).getAllByRole('option')).toHaveLength(5)
    expect(screen.getByRole('button', { name: 'Remove Sent' })).toBeInTheDocument()
    expect(box()).toHaveAttribute('aria-expanded', 'true')
  })

  it('the first row is ready, arrows move it, and a tag goes with its x or Backspace', async () => {
    render(<Form onSearch={vi.fn()} />)
    fireEvent.keyDown(box(), { key: 'ArrowDown' })
    expect(await screen.findByRole('option', { name: 'To Fulfill' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(box(), { key: 'ArrowDown' })
    expect(screen.getByRole('option', { name: 'Sent' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(box(), { key: 'Enter' })
    fireEvent.click(screen.getByRole('button', { name: 'Remove Sent' }))
    expect(screen.queryByRole('button', { name: 'Remove Sent' })).toBeNull()
    fireEvent.keyDown(box(), { key: 'ArrowDown' })
    fireEvent.keyDown(box(), { key: 'Enter' })
    expect(screen.getByRole('button', { name: 'Remove To Fulfill' })).toBeInTheDocument()
    fireEvent.keyDown(box(), { key: 'Backspace' })
    expect(screen.queryByRole('button', { name: 'Remove To Fulfill' })).toBeNull()
  })

  it('what matches nothing says so', async () => {
    render(<Form onSearch={vi.fn()} />)
    fireEvent.change(box(), { target: { value: 'zzz' } })
    expect(await screen.findByText('No match')).toBeInTheDocument()
  })

  it('Enter on a real button is that button, not a search', () => {
    const onSearch = vi.fn()
    render(<Form onSearch={onSearch} />)
    fireEvent.keyDown(screen.getByRole('button', { name: 'Clear' }), { key: 'Enter' })
    expect(onSearch).not.toHaveBeenCalled()
  })
})

describe('other fields under the same rule', () => {
  it('a closed select does not reopen on Enter: it searches', () => {
    const onSearch = vi.fn()
    render(
      <form onKeyDownCapture={submitOnEnter} onSubmit={(e) => { e.preventDefault(); onSearch() }}>
        <Select aria-label="How" value="a" onChange={() => {}} options={[{ value: 'a', label: 'A' }]} />
      </form>)
    fireEvent.keyDown(screen.getByRole('combobox', { name: 'How' }), { key: 'Enter' })
    expect(onSearch).toHaveBeenCalledOnce()
  })

  it('a tag box adds what was typed, then Enter searches', () => {
    const onSearch = vi.fn()
    function Tags() {
      const [values, setValues] = useState<string[]>([])
      const [draft, setDraft] = useState('')
      return (
        <form onKeyDownCapture={submitOnEnter} onSubmit={(e) => { e.preventDefault(); onSearch(values) }}>
          <TagInput label="Code" values={values} onChange={setValues} draft={draft} onDraftChange={setDraft} />
        </form>)
    }
    render(<Tags />)
    const input = screen.getByRole('textbox', { name: 'Code' })
    fireEvent.change(input, { target: { value: 'CF0092' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSearch).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSearch).toHaveBeenCalledWith(['CF0092'])
  })
})

describe('MultiChoice', () => {
  it('is the tags box, whatever the length; a page can ask for checkboxes', () => {
    const { rerender } = render(<MultiChoice label="L" options={OPTIONS.slice(0, 3)} values={[]} onChange={() => {}} />)
    expect(screen.getByRole('combobox', { name: 'L' })).toBeInTheDocument()
    rerender(<MultiChoice label="L" display="checkboxes" options={OPTIONS} values={[]} onChange={() => {}} />)
    expect(screen.getAllByRole('checkbox')).toHaveLength(7)
  })
})

describe('suggestions from other records', () => {
  function Suggest({ load, onDraft }: { load: (q: string) => Promise<{ value: string; label: string }[]>; onDraft?: (d: string) => void }) {
    const [values, setValues] = useState<string[]>([])
    return <MultiSelect label="Partner" options={[]} freeText loadOptions={load} values={values} onChange={setValues} onDraftChange={onDraft} />
  }
  const input = () => screen.getByRole('combobox', { name: 'Partner' })

  it('reads the values for what is typed, and offers the typed text first', async () => {
    const load = vi.fn(async () => [{ value: 'Walmart', label: 'Walmart' }])
    render(<Suggest load={load} />)
    fireEvent.change(input(), { target: { value: 'wal' } })
    expect(await screen.findByRole('option', { name: /Walmart/ })).toBeInTheDocument()
    expect(load).toHaveBeenCalledWith('wal')
    expect(screen.getByRole('option', { name: /wal.*as typed/ })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(input(), { key: 'ArrowDown' })
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(screen.getByRole('button', { name: 'Remove Walmart' })).toBeInTheDocument()
  })

  it('Enter keeps the text as typed when no suggestion is wanted', async () => {
    render(<Suggest load={async () => []} />)
    fireEvent.change(input(), { target: { value: 'CF00' } })
    await screen.findByRole('option', { name: /CF00/ })
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(screen.getByRole('button', { name: 'Remove CF00' })).toBeInTheDocument()
  })

  it('tells its owner what is typed, so a Search button can add it', () => {
    const onDraft = vi.fn()
    render(<Suggest load={async () => []} onDraft={onDraft} />)
    fireEvent.change(input(), { target: { value: 'x' } })
    expect(onDraft).toHaveBeenCalledWith('x')
  })

  it('TagInput: pasted text with commas adds each value, and what follows the last comma stays typed', () => {
    const seen: string[][] = []
    function Tags() {
      const [values, setValues] = useState<string[]>(['a@x.com'])
      const [draft, setDraft] = useState('')
      return <TagInput label="To" values={values} onChange={(v) => { seen.push(v); setValues(v) }} draft={draft} onDraftChange={setDraft} />
    }
    render(<Tags />)
    const input = screen.getByRole('textbox', { name: 'To' })
    fireEvent.change(input, { target: { value: 'b@y.com, c@z.com,' } })
    expect(seen.at(-1)).toEqual(['a@x.com', 'b@y.com', 'c@z.com'])
    expect(input).toHaveValue('')
    fireEvent.change(input, { target: { value: 'd@w.com, e@' } })
    expect(seen.at(-1)).toEqual(['a@x.com', 'b@y.com', 'c@z.com', 'd@w.com'])
    expect(input).toHaveValue('e@')
  })
})
