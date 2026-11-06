import { describe, expect, it } from 'vitest'
import { invoiceClaims, poLinesSubtotal } from './invoiceClaims'

const pa = (pa_number: string, pa_type: string, status: string, invoice_ids: string[]) =>
  ({ pa_number, pa_type, status, invoice_ids })

describe('invoiceClaims', () => {
  const prepay = pa('PA-1', 'prepayment', 'processed', ['inv-1'])

  it('locks an invoice on a live PA and names it', () => {
    expect(invoiceClaims([prepay], 'regular').get('inv-1')).toBe('PA-1')
  })

  it('releases invoices on cancelled and rejected PAs', () => {
    const pas = [pa('PA-2', 'regular', 'cancelled', ['inv-1']), pa('PA-3', 'regular', 'rejected', ['inv-2'])]
    expect(invoiceClaims(pas, 'regular').size).toBe(0)
  })

  it.each(['settlement', 'balance'])('lets a %s take the invoice of a prepayment', (type) => {
    expect(invoiceClaims([prepay], type).has('inv-1')).toBe(false)
  })

  it('still locks a settlement against another settlement or a regular PA', () => {
    const pas = [pa('PA-4', 'settlement', 'approved', ['inv-1']), pa('PA-5', 'regular', 'draft', ['inv-2'])]
    const claims = invoiceClaims(pas, 'settlement')
    expect(claims.get('inv-1')).toBe('PA-4')
    expect(claims.get('inv-2')).toBe('PA-5')
  })
})

describe('poLinesSubtotal', () => {
  it('adds string amounts as numbers', () => {
    expect(poLinesSubtotal([{ line_total: '100.00' }, { line_total: '50.50' }])).toBe(150.5)
  })

  it('returns a number for a single string amount', () => {
    expect(poLinesSubtotal([{ line_total: '100.00' }]).toFixed(2)).toBe('100.00')
  })

  it('is zero for no lines', () => {
    expect(poLinesSubtotal([])).toBe(0)
  })
})
