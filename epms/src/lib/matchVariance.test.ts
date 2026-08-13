import { describe, expect, it } from 'vitest'
import { matchMode, buildLineComparisons } from './matchVariance'
import type { ApiInvoice, InvoiceAllocation } from '@/services/invoices'
import type { ApiPoLineItem } from '@/services/po'

// ── Fixture builders ────────────────────────────────────────────────────────
// Minimal literal objects, not imports from production fixtures (brief
// requirement) — only the fields buildLineComparisons/matchMode actually read
// are filled in; everything else on ApiInvoice/InvoiceAllocation/ApiPoLineItem
// is cast away with `as unknown as` to keep the fixtures short.

function makeAllocation(overrides: Partial<InvoiceAllocation>): InvoiceAllocation {
  return {
    id: 'alloc-1',
    invoice_id: 'inv-1',
    invoice_line_id: 'line-1',
    po_id: 'po-1',
    po_line_id: 'pol-1',
    allocated_amount: 1000,
    allocated_tax: 0,
    allocated_total: 1000,
    variance: null,
    variance_pct: null,
    note: null,
    ...overrides,
  }
}

function makeInvoice(overrides: Partial<ApiInvoice>): ApiInvoice {
  return {
    id: 'inv-1',
    internal_ref: 'INV-0001',
    vendor_invoice_number: 'V-1',
    vendor_id: 'vendor-1',
    vendor_name: 'Acme',
    amount: 1000,
    tax_amount: 0,
    total_amount: 1000,
    currency: 'CAD',
    invoice_date: '2026-08-01',
    due_date: '2026-09-01',
    line_items: [
      { id: 'line-1', description: 'Widget', quantity: 10, unit: 'ea', unit_price: 100, line_total: 1000 },
    ],
    status: 'matched',
    uploaded_at: '2026-08-01T00:00:00Z',
    uploaded_by: 'user-1',
    created_at: '2026-08-01T00:00:00Z',
    allocations: [],
    ...overrides,
  } as unknown as ApiInvoice
}

function makePoLine(overrides: Partial<ApiPoLineItem>): ApiPoLineItem {
  return {
    id: 'pol-1',
    description: 'Widget PO Line',
    qty: 10,
    unit: 'ea',
    unit_price: 95,
    line_total: 950,
    received_qty: 10,
    ...overrides,
  }
}

describe('matchMode', () => {
  it('returns by-line when every allocation carries a non-null po_line_id', () => {
    const allocations = [
      makeAllocation({ id: 'a1', po_line_id: 'pol-1' }),
      makeAllocation({ id: 'a2', po_line_id: 'pol-2' }),
    ]
    expect(matchMode(allocations)).toBe('by-line')
  })

  it('returns by-amount when allocations have po_line_id === null', () => {
    const allocations = [
      makeAllocation({ id: 'a1', po_line_id: null }),
      makeAllocation({ id: 'a2', po_line_id: null }),
    ]
    expect(matchMode(allocations)).toBe('by-amount')
  })

  it('returns by-amount when even one allocation among several lacks a po_line_id (mixed is not by-line)', () => {
    const allocations = [
      makeAllocation({ id: 'a1', po_line_id: 'pol-1' }),
      makeAllocation({ id: 'a2', po_line_id: null }),
    ]
    expect(matchMode(allocations)).toBe('by-amount')
  })
})

describe('buildLineComparisons', () => {
  it('joins invoice line -> allocation -> PO line and computes a per-row variance', () => {
    const invoice = makeInvoice({
      allocations: [makeAllocation({ id: 'a1', invoice_line_id: 'line-1', po_line_id: 'pol-1', allocated_amount: 1000 })],
    })
    const poLines = [makePoLine({ id: 'pol-1', line_total: 950 })]

    const rows = buildLineComparisons(invoice, poLines)

    expect(rows).toHaveLength(1)
    expect(rows[0]).toEqual({
      allocationId: 'a1',
      invoiceLineDescription: 'Widget',
      invoiceQty: 10,
      invoiceUnitPrice: 100,
      invoiceAmount: 1000,
      poLineDescription: 'Widget PO Line',
      poQty: 10,
      poUnitPrice: 95,
      poAmount: 950,
      variance: 50, // 1000 - 950, since the allocation carries no backend variance
    })
  })

  it('coerces a Decimal-as-string allocated_amount with Number() before arithmetic', () => {
    const invoice = makeInvoice({
      allocations: [
        makeAllocation({
          id: 'a1',
          invoice_line_id: 'line-1',
          po_line_id: 'pol-1',
          // Simulates the real wire shape: Pydantic serialises Decimal as a
          // JSON string. Cast past the (number) type to model that payload.
          allocated_amount: '900.00' as unknown as number,
        }),
      ],
    })
    const poLines = [makePoLine({ id: 'pol-1', line_total: 1000 })]

    const rows = buildLineComparisons(invoice, poLines)

    // If this concatenated instead of adding, the result would be the
    // string "900.001000" or NaN — assert the real arithmetic result.
    expect(rows[0].invoiceAmount).toBe(900)
    expect(rows[0].poAmount).toBe(1000)
    expect(rows[0].variance).toBe(-100)
  })

  it('yields poAmount: null (not a throw) when the allocation\'s po_line_id is absent from poLines', () => {
    const invoice = makeInvoice({
      allocations: [
        makeAllocation({ id: 'a1', invoice_line_id: 'line-1', po_line_id: 'pol-missing', allocated_amount: 500 }),
      ],
    })
    const poLines = [makePoLine({ id: 'pol-1' })] // does not contain 'pol-missing'

    expect(() => buildLineComparisons(invoice, poLines)).not.toThrow()
    const rows = buildLineComparisons(invoice, poLines)
    expect(rows[0].poAmount).toBeNull()
    expect(rows[0].poLineDescription).toBeNull()
    // No PO line to compare against — falls back to the invoice amount
    // rather than fabricating a zero/matched variance.
    expect(rows[0].variance).toBe(500)
  })

  it('prefers the backend-computed allocation.variance when present, over local arithmetic', () => {
    const invoice = makeInvoice({
      allocations: [
        makeAllocation({
          id: 'a1', invoice_line_id: 'line-1', po_line_id: 'pol-1', allocated_amount: 1000,
          variance: '25.00' as unknown as number,
        }),
      ],
    })
    const poLines = [makePoLine({ id: 'pol-1', line_total: 950 })]

    const rows = buildLineComparisons(invoice, poLines)
    expect(rows[0].variance).toBe(25)
  })
})
