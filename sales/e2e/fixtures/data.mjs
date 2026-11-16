// Synthetic data for the responsive check (sales/e2e/responsive.mjs): invented values only —
// no dev2 data, no masked dumps. Shapes follow the API's read schemas.

const addr = { name: 'Sample customer', address1: '1 Sample St', city: 'Toronto', province: 'ON', zip: 'M5V 1A1', country: 'CA' }
const STATUSES = ['To Fulfill', 'Fulfilling', 'Shipped', 'Completed']

const orderRow = (n) => ({
  id: n, order_number: n, version: 1, source_type: 'shopify', source_name: 'Shopify', source_reference: `SMP-${1000 + n}`,
  source_order_id: `${5000 + n}`, source_order_number: `${1000 + n}`, partner_code: 'SAMPLE', partner_name: 'Sample partner',
  order_date: '2026-11-05', created_at: '2026-11-05T12:00:00Z', updated_at: '2026-11-05T12:00:00Z', first_received_at: '2026-11-05T12:00:00Z',
  source_updated_at: '2026-11-05T12:00:00Z', paid_at: '2026-11-05T12:00:00Z', requested_delivery_date: null,
  order_state: 'open', order_status: STATUSES[n % STATUSES.length], order_status_label: STATUSES[n % STATUSES.length],
  next_step: 'Confirm delivery once it has been delivered.', next_step_who: 'you',
  status_detail: 'Since Nov 5, 2026', status_detail_at: '2026-11-05T12:00:00Z', fulfillment_status: null, invoice_status: null,
  shopify_financial_status: 'paid', shopify_fulfillment_status: null, hold_reason: null, on_hold_reason: null, review_reason: null,
  currency: 'CAD', total: '120.00', total_amount: '120.00', original_total: '120.00', net_amount: '120.00', total_refunded: '0.00',
  backordered_ea: 0, short_shipped: false, shadow: false, historical: false, archive_suppressed: false, archived_in_shopify_at: null,
  source_cancelled_at: null, tags: [], ship_to: addr, payments: [], refunds: [], documents: [], timeline: [],
})

export const orders = Array.from({ length: 30 }, (_, i) => orderRow(30 - i))

const packing = (line) => [{ shipment_id: 1, document_id: 1, sscc: '00000000000000000017', lot: `LOT-${line}`, expiry: '2028-01-31',
  qty_shipped_ea: 120, tracking: null, ship_date: '2026-11-06', applied: true, shadow: false }]

const line = (i) => ({
  id: 100 + i, line_no: i, item_code: `ITEM-${i}`, source: { item_code: `ITEM-${i}` }, ordered_qty: '120.0000', order_unit: 'EA', factor_ea: '1.0000',
  qty_ea: 120, unit_price: '1.00', invoice_unit_price: null, line_status: 'Active', removed: false, problem: null, tax_amount: null,
  discount_amount: null, tax_lines: null, shipped_ea: 120, delivered_qty: null, invoiced_qty: null, invoice_unit: null, invoice_price: null,
  invoice_amount: null, state: 'Shipped', backordered_ea: 0, shipped_shadow_ea: null, backordered_shadow_ea: null, packing: packing(i),
})

export const orderDetail = (id) => ({
  ...orderRow(id), order_type: 'standard', bill_to: addr, contact: null, notes: null, dc_number: null, department: null,
  discount: '0.00', discount_codes: [], payment_gateways: [], subtotal: '120.00', shipping: '0.00', tax: '0.00', tax_lines: [],
  has_invoice: false, cancellable: true, hold_exits: [], ship_to_flags: [], shipments: [], lines: [line(1), line(2)],
})

const weeks = ['2026-11-09', '2026-11-02']
export const payments = weeks.flatMap((w, wi) => [0, 1, 2, 3].map((i) => {
  const id = wi * 4 + i + 1
  const refund = i === 3
  const amt = (40 + i * 13.5).toFixed(2)
  return { id, payment_row_id: id, order_id: 1, order_ref: `SMP-${1000 + id}`, customer: 'Sample customer', processed_at: `${w}T1${i}:15:00Z`,
    processed_week: w, processed_month: '2026-11-01', method: 'PayPal', gateway: 'paypal', kind: refund ? 'refund' : 'sale', status: 'success',
    amount: amt, currency: 'CAD', sales_amount: refund ? '0.00' : amt, refund_amount: refund ? amt : '0.00', net_amount: refund ? `-${amt}` : amt,
    gateway_ref: `PP-${1000 + id}`, shopify_payment_id: `${7000 + id}`, order_source: 'web', financial_status: 'paid', order_cancelled: false,
    issue: null, discount: '0.00', shipping: '0.00', order_total: amt, test: false }
}))
const sum = (rows, f) => rows.reduce((a, r) => a + Number(r[f]), 0).toFixed(2)
export const totalsOf = (rows) => [{ currency: 'CAD', count: rows.length,
  sums: { sales_amount: sum(rows, 'sales_amount'), refund_amount: sum(rows, 'refund_amount'), net_amount: sum(rows, 'net_amount') } }]
export const paymentGroups = weeks.map((w) => ({ key: [w], count: 4, totals: totalsOf(payments.filter((p) => p.processed_week === w)) }))

export const stockItems = ['ITEM-1', 'ITEM-2', 'ITEM-3'].map((c, i) => ({ id: i + 1, item_code: c, description: `Sample item ${i + 1}`,
  received_ea: 1200, returned_ea: 0, shipped_ea: 700, adjusted_ea: 0, report_diff_ea: 0, on_hand_ea: 500, hold_ea: 0, allocated_ea: 20,
  available_ea: 480, to_radiant_ea: 0, to_customer_ea: 12, returning_ea: 0, lots: 2, first_expiry: '2027-01-15', days_to_first_expiry: 60,
  last_move_at: '2026-11-12T15:00:00Z' }))
export const stockLots = (code) => [0, 1].map((j) => ({ id: j + 1, item_code: code, lot: `LOT-${j + 1}`, expiry: `2027-0${j + 1}-15`,
  days_to_expiry: 60 + j * 30, received_ea: 600, returned_ea: 0, shipped_ea: 350, adjusted_ea: 0, report_diff_ea: 0, on_hand_ea: 250,
  hold_ea: 0, in_process_ea: 0, available_ea: 250, to_customer_ea: 6, returning_ea: 0, to_radiant_ea: 0,
  first_move_at: '2026-10-01T12:00:00Z', last_move_at: '2026-11-12T15:00:00Z' }))

export const partners = [{ id: 1, code: 'SAMPLE', name: 'Sample partner', channel: 'MANUAL', active: true }]
export const items = [{ id: 1, code: 'ITEM-1', item_code: 'ITEM-1', description: 'Sample item', active: true, version: 1,
  units: [{ unit: 'EA', factor_ea: '1.0000' }, { unit: 'CS', factor_ea: '6.0000' }] }]

const link = (page) => ({ page, resource: page.slice(1), filters: [] })
export const health = {
  result: 'WARNING', checked_at: '2026-11-16T12:00:00Z', shadow_mode: false, partner_writes_enabled: false, external_email_enabled: false,
  scheduler_enabled: true, scheduler_paused: false, system_minutes: 5, gate_ok: true, cards: { open_orders: 12, needs_review: 2 },
  checks: [
    { key: 'sftp', label: 'Warehouse connection', result: 'PASSED', detail: 'Last run 5 minutes ago', count: null, link: null },
    { key: 'ack', label: 'Acknowledgements on time', result: 'WARNING', detail: 'One acknowledgement is late: a long explanation that has to wrap on a phone screen', count: 1, link: link('/orders') },
  ],
  gate: [{ event_type: 'order_failed', label: 'Order failed', rule_id: 1, recipients: 2, tested_at: '2026-11-15T10:00:00Z', ok: true, problem: null }],
  jobs: [{ scope: 'system', label: 'System job', connection_id: null, interval_minutes: 5, schedule: 'every 5 min', paused: false,
    last_run_at: '2026-11-16T11:55:00Z', last_run_status: 'SUCCESS', last_run_summary: 'nothing to do', next_run_at: '2026-11-16T12:00:00Z', overdue: false }],
}
export const dashboard = {
  cards: [
    { key: 'open', label: 'Open orders', count: 12, link: link('/orders'), parts: [] },
    { key: 'review', label: 'Needs review', count: 2, link: link('/orders'), parts: [] },
    { key: 'invoice', label: 'To invoice', count: 3, link: link('/invoices'), parts: [] },
  ],
  open_tasks: [{ type: 'order_review', count: 2 }], needs_review: 2, waiting_delivery: 3,
  runs: [{ id: 1, trigger: 'schedule', status: 'SUCCESS', started_at: '2026-11-16T11:55:00Z', finished_at: '2026-11-16T11:55:04Z', error: null }],
}
