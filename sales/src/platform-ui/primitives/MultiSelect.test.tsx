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
const search = async () => screen.findByRole('textbox', { name: 'Status: search' })

describe('MultiSelect and the Enter rule', () => {
  it('Enter chooses while the list is open, then confirms and searches', async () => {
    const onSearch = vi.fn()
    render(<Form onSearch={onSearch} />)
    fireEvent.click(box())
    const input = await search()
    fireEvent.change(input, { target: { value: 'ship' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSearch).not.toHaveBeenCalled()  // the first Enter chooses
    expect(box()).toHaveTextContent('Shipped')
    expect(box()).toHaveAttribute('aria-expanded', 'false')
    fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSearch).toHaveBeenCalledWith(['Shipped'])  // the second Enter searches
  })

  it('a click chooses several and stays open; more than two shows a count', async () => {
    render(<Form onSearch={vi.fn()} />)
    fireEvent.click(box())
    const list = await screen.findByRole('listbox', { name: 'Status' })
    for (const name of ['Sent', 'Shipped', 'On Hold']) fireEvent.click(within(list).getByRole('option', { name }))
    expect(within(list).getByRole('option', { name: 'Sent' })).toHaveAttribute('aria-selected', 'true')
    expect(box()).toHaveTextContent('Sent +2')
  })

  it('arrows reach a row, Space chooses and stays open', async () => {
    render(<Form onSearch={vi.fn()} />)
    fireEvent.click(box())
    const input = await search()
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: ' ' })
    expect(screen.getByRole('option', { name: 'To Fulfill' })).toHaveAttribute('aria-selected', 'true')
    expect(box()).toHaveAttribute('aria-expanded', 'true')
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
  it('a short list shows its checkboxes, a long one a dropdown; the page can choose', () => {
    const { rerender } = render(<MultiChoice label="L" options={OPTIONS.slice(0, 3)} values={[]} onChange={() => {}} />)
    expect(screen.getAllByRole('checkbox')).toHaveLength(3)
    rerender(<MultiChoice label="L" options={OPTIONS} values={[]} onChange={() => {}} />)
    expect(screen.getByRole('combobox', { name: 'L' })).toBeInTheDocument()
    rerender(<MultiChoice label="L" display="checkboxes" options={OPTIONS} values={[]} onChange={() => {}} />)
    expect(screen.getAllByRole('checkbox')).toHaveLength(7)
  })
})
