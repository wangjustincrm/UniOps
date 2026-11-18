import { act, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { Badge, createStatusBadge } from './badge'
import { Button } from './button'
import { ConfirmDialog, Modal } from './overlay'
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from './table'
import { Select } from './controls'

describe('Button', () => {
  it('loading disables it so a double click cannot fire twice', () => {
    const onClick = vi.fn()
    render(<Button loading onClick={onClick}>Save</Button>)
    const btn = screen.getByRole('button', { name: /save/i })
    expect(btn).toBeDisabled()
    fireEvent.click(btn)
    expect(onClick).not.toHaveBeenCalled()
  })
  it('keeps the HTML default type so existing forms still submit', () => {
    render(<Button>Go</Button>)
    expect(screen.getByRole('button')).not.toHaveAttribute('type')
  })
})

describe('Badge / createStatusBadge', () => {
  const { StatusBadge, statusLabel } = createStatusBadge({
    approved: { label: 'Approved', tone: 'success' },
    voided: { label: 'Removed', tone: 'dark' },
  })
  it('one table gives the word and the tone', () => {
    const { container } = render(<StatusBadge status="voided" />)
    expect(container.textContent).toBe('Removed')
    expect(statusLabel('approved')).toBe('Approved')
  })
  it('unknown status falls back to the raw value, neutral', () => {
    const { container } = render(<StatusBadge status="mystery" />)
    expect(container.textContent).toBe('mystery')
    expect(container.firstElementChild!.className).toContain('bg-surface-sunken')
  })
  it('renders only semantic colour classes', () => {
    const { container } = render(<><Badge variant="danger" dot>x</Badge><Badge variant="warning">y</Badge></>)
    expect(container.innerHTML).not.toMatch(/(?:bg|text)-(?:red|amber|green|neutral|gray)-\d/)
  })
})

describe('Modal', () => {
  it('Escape closes; busy blocks Escape', () => {
    const onClose = vi.fn()
    const { rerender } = render(<Modal open onClose={onClose} title="T">body</Modal>)
    expect(screen.getByRole('dialog')).toHaveAccessibleName('T')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    rerender(<Modal open busy onClose={onClose} title="T">body</Modal>)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
  it('renders nothing when closed', () => {
    render(<Modal open={false} onClose={() => {}} title="T">body</Modal>)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('ConfirmDialog', () => {
  function Harness({ action }: { action: () => unknown }) {
    const [open, setOpen] = useState(true)
    return open ? <ConfirmDialog title="Void" message="Sure?" confirmLabel="Void" onConfirm={action} onClose={() => setOpen(false)} /> : <p>closed</p>
  }
  it('a failing action keeps the dialog open and shows the error', async () => {
    render(<Harness action={() => Promise.reject({ response: { data: { detail: 'Already paid' } } })} />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Void' })) })
    expect(screen.getByRole('alert')).toHaveTextContent('Already paid')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
  it('a successful action closes it', async () => {
    render(<Harness action={() => Promise.resolve()} />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Void' })) })
    expect(screen.getByText('closed')).toBeInTheDocument()
  })
})

describe('Table', () => {
  it('locks the header and uses sentence-case header cells', () => {
    const { container } = render(
      <TableScroll><Table><THead><Tr><Th>Number</Th><Th align="right">Amount</Th></Tr></THead>
        <TBody><Tr onClick={() => {}}><Td>PO-1</Td><Td numeric>1.00</Td></Tr></TBody></Table></TableScroll>,
    )
    const thead = container.querySelector('thead')!
    expect(thead.className).toContain('sticky')
    expect(thead.className).toContain('top-0')
    const th = container.querySelectorAll('th')
    expect(th[0].className).not.toContain('uppercase')
    expect(th[1].className).toContain('text-right')
    expect(container.querySelector('tbody tr')!.className).toContain('cursor-pointer')
    expect(container.querySelectorAll('td')[1].className).toContain('tabular-nums')
  })
})

describe('Select', () => {
  it('className sizes the wrapper; the select stays full width', () => {
    const { container } = render(<Select className="w-40" defaultValue="a"><option value="a">A</option></Select>)
    expect(container.firstElementChild!.className).toContain('w-40')
    expect(container.querySelector('select')!.className).toContain('w-full')
  })
})
