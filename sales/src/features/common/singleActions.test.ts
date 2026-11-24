import { beforeEach, describe, expect, it, vi } from 'vitest'

// Each single button's call is its record action (stage 3 l): the list, the action, the version seen, why and the input.
const http = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: http }))
const orders = await import('@/features/orders/ordersApi')
const invoices = await import('@/features/invoices/invoicesApi')
const transfers = await import('@/features/transfers/transfersApi')
const documents = await import('@/features/operations/operationsApi')
const counts = await import('@/features/inventory/inventoryApi')

const CALLS: [string, () => Promise<unknown>, string, object][] = [
  ['cancel order', () => orders.cancelOrder(7, 3, 'why'), '/orders/actions/cancel', { ids: { 7: 3 }, reason: 'why' }],
  ['release hold', () => orders.releaseHold(7, 3, 'Shipped', 'why'), '/orders/actions/release_hold', { ids: { 7: 3 }, input: { to: 'Shipped' }, reason: 'why' }],
  ['re-check', () => orders.resolveReview(7, 3), '/orders/actions/recheck', { ids: { 7: 3 } }],
  ['submit draft', () => orders.submitManual(7, 3), '/orders/actions/submit_draft', { ids: { 7: 3 } }],
  ['cancel draft', () => orders.cancelManual(7, 3), '/orders/actions/cancel_draft', { ids: { 7: 3 } }],
  ['send 940', () => orders.send940(7, 3), '/orders/actions/send_940', { ids: { 7: 3 } }],
  ['replace 940', () => orders.replace940(7, 3, 'why'), '/orders/actions/replace_940', { ids: { 7: 3 }, reason: 'why' }],
  ['fulfil', () => orders.fulfilInShopify(7, 3), '/orders/actions/fulfil_in_shopify', { ids: { 7: 3 } }],
  ['send invoice', () => invoices.sendInvoice(7, 3), '/invoices/actions/send', { ids: { 7: 3 } }],
  ['send invoice again', () => invoices.sendInvoiceAgain(7, 3, 'why'), '/invoices/actions/send_again', { ids: { 7: 3 }, reason: 'why' }],
  ['transfer ready', () => transfers.readyTransfer(7, 3), '/transfers/actions/ready', { ids: { 7: 3 } }],
  ['transfer cancel', () => transfers.cancelTransfer(7, 3, 'why'), '/transfers/actions/cancel', { ids: { 7: 3 }, reason: 'why' }],
  ['transfer release', () => transfers.releaseTransfer(7, 3, 'why'), '/transfers/actions/release', { ids: { 7: 3 }, reason: 'why' }],
  ['transfer resend', () => transfers.resendTransfer(7, 3), '/transfers/actions/resend', { ids: { 7: 3 } }],
  ['read again', () => documents.readAgain(7), '/documents/actions/read_again', { ids: { 7: null } }],
  ['ignore', () => documents.ignoreDocument(7, 'why'), '/documents/actions/ignore', { ids: { 7: null }, reason: 'why' }],
  ['send again', () => documents.sendAgain(7), '/documents/actions/send_again', { ids: { 7: null } }],
  ['send again with why', () => documents.sendAgain(7, 'why'), '/documents/actions/send_again', { ids: { 7: null }, reason: 'why' }],
  ['count apply', () => counts.applyCount(7, 3), '/inventory/counts/actions/apply', { ids: { 7: 3 } }],
  ['count undo', () => counts.undoCount(7, 3, 'why'), '/inventory/counts/actions/undo', { ids: { 7: 3 }, reason: 'why' }],
]
const answer = (row: object) => ({ action: 'x', dry_run: false, counts: {}, results: [{ id: 7, ...row }] })

describe('each single button', () => {
  beforeEach(() => { http.post.mockReset() })
  it.each(CALLS)('%s', async (_, call, path, body) => {
    http.post.mockResolvedValue(answer({ status: 'done' }))
    await call()
    expect(http.post.mock.calls).toEqual([[path, body]])
    for (const status of ['skipped', 'failed']) {
      http.post.mockResolvedValue(answer({ status, reason: 'not now' }))
      await expect(call()).rejects.toThrow('not now')  // never a success
    }
  })
})
