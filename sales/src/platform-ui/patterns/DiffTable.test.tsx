import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { DiffTable } from './DiffTable'

describe('DiffTable', () => {
  it('highlights the differing rows and shows a missing side as —', () => {
    render(<DiffTable ariaLabel="Segments" leftLabel="Before" rightLabel="After" rows={[
      { left: 'W05*N*9471', right: 'W05*N*9471', same: true },
      { left: 'W01*18*EA', right: 'W01*12*EA', same: false },
      { left: 'W01*1*EA', right: null, same: false }]} />)
    const t = screen.getByRole('table', { name: 'Segments' })
    const [same, changed, missing] = within(t).getAllByRole('row').slice(1)
    expect(same).not.toHaveAttribute('data-differs')
    expect(changed).toHaveAttribute('data-differs', 'true')
    expect(within(missing).getByText('—')).toBeInTheDocument()
    expect(within(t).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Before', 'After'])
  })
})
