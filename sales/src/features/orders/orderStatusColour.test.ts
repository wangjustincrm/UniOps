import { describe, expect, it } from 'vitest'
import { orderStatusColour } from './orderStatus'

describe('order status colours', () => {
  const statuses = ['To Fulfill', 'Sent', 'Fulfilling', 'Shipped', 'To Invoice', 'Completed', 'On Hold']
  it('gives every working stage its own colour', () => {
    expect(new Set(statuses.map(orderStatusColour)).size).toBe(statuses.length)
  })
  it('an unknown status still gets a readable badge', () => {
    expect(orderStatusColour('Something new')).toContain('bg-neutral')
  })
})
