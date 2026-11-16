import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { NextStep } from './OrderStatusCells'

// The order's next step (the user's approved table, 2026-11-16): the server's text and whose turn it is.
describe('NextStep', () => {
  it('says whose turn it is and what to do', () => {
    render(<NextStep o={{ next_step: 'Confirm delivery once it has been delivered.', next_step_who: 'you' }} />)
    expect(screen.getByText('Your turn')).toBeInTheDocument()
    expect(screen.getByText('Confirm delivery once it has been delivered.')).toBeInTheDocument()
  })
  it('waiting on someone else, or nothing to do; no step: nothing', () => {
    const { rerender, container } = render(<NextStep o={{ next_step: "Waiting for the warehouse's 945.", next_step_who: 'warehouse' }} />)
    expect(screen.getByText('Waiting on warehouse')).toBeInTheDocument()
    rerender(<NextStep o={{ next_step: 'The invoice goes out on the next run.', next_step_who: 'nobody' }} />)
    expect(screen.getByText('Nothing to do')).toBeInTheDocument()
    rerender(<NextStep o={{ next_step: null, next_step_who: null }} />)
    expect(container).toBeEmptyDOMElement()
  })
})
