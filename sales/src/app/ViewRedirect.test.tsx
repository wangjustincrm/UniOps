import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { takeViewRequest } from '@/platform-ui/resource/viewRequests'
import { ViewRedirect } from './ViewRedirect'

describe('the old /review address', () => {
  it('goes to Orders and asks it for the Needs review view', async () => {
    render(
      <MemoryRouter initialEntries={['/review']}>
        <Routes>
          <Route path="/review" element={<ViewRedirect to="/orders" resource="orders" view="needs_review" />} />
          <Route path="/orders" element={<p>orders list</p>} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText('orders list')).toBeInTheDocument()
    expect(takeViewRequest('orders')).toBe('needs_review')
    expect(takeViewRequest('orders')).toBeUndefined()  // taken once
  })
})
