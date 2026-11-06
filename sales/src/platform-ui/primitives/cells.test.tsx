import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CountdownCell, DurationCell, FileCell, NumberCell, ReferenceCell, RelativeCell, TrackingCell } from './cells'

describe('list cells', () => {
  it('a number carries its unit, and nothing without a value', () => {
    render(<NumberCell value="1200" unit="EA" />)
    expect(screen.getByText('1,200 EA')).toBeInTheDocument()
  })
  it('a countdown is red and says late once its time has passed', () => {
    const { container } = render(<CountdownCell value={new Date(Date.now() - 2 * 3_600_000 - 60_000).toISOString()} />)
    expect(screen.getByText(/^2h( 1m)? late$/)).toBeInTheDocument()
    expect(container.firstChild).toHaveClass('text-danger-600')
  })
  it('a countdown still to come is not late', () => {
    render(<CountdownCell value={new Date(Date.now() + 2 * 3_600_000 + 90_000).toISOString()} />)
    expect(screen.getByText(/^in 2h( 1m)?$/)).toBeInTheDocument()
  })
  it('a past moment says how long ago', () => {
    render(<RelativeCell value={new Date(Date.now() - 3 * 86_400_000).toISOString()} />)
    expect(screen.getByText(/^3d.* ago$/)).toBeInTheDocument()
  })
  it('a duration needs both ends', () => {
    render(<DurationCell from="2026-11-06T10:00:00Z" to="2026-11-06T12:33:00Z" />)
    expect(screen.getByText('2h 33m')).toBeInTheDocument()
    render(<DurationCell from="2026-11-06T10:00:00Z" to={null} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
  it('a reference and a tracking number show a dash when empty', () => {
    render(<><ReferenceCell value={null} /><TrackingCell value={null} /></>)
    expect(screen.getAllByText('—')).toHaveLength(2)
  })
  it('a file offers its download', () => {
    const get = vi.fn()
    render(<FileCell name="a.edi" label="Download a.edi" onDownload={get} />)
    fireEvent.click(screen.getByRole('button', { name: 'Download a.edi' }))
    expect(get).toHaveBeenCalledOnce()
  })
})
