import { describe, expect, it } from 'vitest'
import { paEditAutoAmounts } from './paEditAmounts'

const base = {
  invoiceCount: 0, derivedTotal: null, invoiceSubtotal: 0, invoiceTax: 0,
  poLinesSubtotal: 0, isAgreementMode: false, currency: 'CAD', taxRate: 0.13,
}

describe('paEditAutoAmounts (ISS-0261)', () => {
  it('no invoice: pre-tax from the ticked PO lines, tax at the PO rate', () => {
    expect(paEditAutoAmounts({ ...base, poLinesSubtotal: 100 })).toEqual({ subtotal: '100', tax: '13.00' })
  })

  it('no invoice and no PO line: both empty', () => {
    expect(paEditAutoAmounts(base)).toEqual({ subtotal: '', tax: '' })
  })

  it('invoices ticked: pre-tax and tax from the invoices, lines ignored', () => {
    expect(paEditAutoAmounts({ ...base, invoiceCount: 1, invoiceSubtotal: 80, invoiceTax: 10.4, poLinesSubtotal: 100 }))
      .toEqual({ subtotal: '80', tax: '10.40' })
  })

  it('line-matched invoices: pre-tax is the derived lines total', () => {
    expect(paEditAutoAmounts({ ...base, invoiceCount: 1, derivedTotal: 60, invoiceSubtotal: 80, invoiceTax: 7.8 }))
      .toEqual({ subtotal: '60', tax: '7.80' })
  })

  it('non-CAD PO with no invoice: no auto tax', () => {
    expect(paEditAutoAmounts({ ...base, poLinesSubtotal: 100, currency: 'USD' })).toEqual({ subtotal: '100', tax: '' })
  })

  it('agreement route with no invoice: nothing to pay', () => {
    expect(paEditAutoAmounts({ ...base, isAgreementMode: true, poLinesSubtotal: 100 })).toEqual({ subtotal: '', tax: '' })
  })
})
