import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Collapsible } from './primitives/Collapsible'
import { FacetFilter } from './primitives/FacetFilter'
import { Kbd } from './primitives/Kbd'

describe('shadcn pieces (2026-11-23)', () => {
  it('Collapsible opens and closes its content', () => {
    render(<Collapsible title="Exception"><p>the stack</p></Collapsible>)
    expect(screen.queryByText('the stack')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Exception/ }))
    expect(screen.getByText('the stack')).toBeInTheDocument()
  })

  it('FacetFilter picks values with their counts; Kbd names a key', async () => {
    const onChange = vi.fn()
    render(<><FacetFilter title="Kind" options={[{ value: 'job', count: 3 }, { value: 'request', count: 9 }]} selected={[]} onChange={onChange} /><Kbd>Esc</Kbd></>)
    fireEvent.click(screen.getByRole('button', { name: 'Kind' }))
    expect(await screen.findByText('9')).toBeInTheDocument()
    fireEvent.click(screen.getByText('job'))
    expect(onChange).toHaveBeenCalledWith(['job'])
    expect(screen.getByText('Esc').tagName).toBe('KBD')
  })
})
