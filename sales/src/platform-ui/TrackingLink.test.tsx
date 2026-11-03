import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TrackingLink } from './index'

describe('TrackingLink', () => {
  it("with the carrier's link: the copy chip and a link opening it in a new tab, without the opener", () => {
    render(<TrackingLink number="EWK 0001/A" url="https://www.purolator.com/en/shipping/tracker?pin=EWK%200001%2FA" />)
    expect(screen.getByRole('button', { name: 'Copy EWK 0001/A' })).toBeInTheDocument()
    const a = screen.getByRole('link', { name: "Track EWK 0001/A on the carrier's site (new tab)" })
    expect(a).toHaveAttribute('href', 'https://www.purolator.com/en/shipping/tracker?pin=EWK%200001%2FA')
    expect(a).toHaveAttribute('target', '_blank')
    expect(a).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('an unknown carrier: the copy chip only', () => {
    render(<TrackingLink number="ZZ1" url={null} />)
    expect(screen.getByRole('button', { name: 'Copy ZZ1' })).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
  })
})
