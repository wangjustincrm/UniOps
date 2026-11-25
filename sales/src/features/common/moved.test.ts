import { describe, expect, it, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'
import { ordersClient } from '@/features/orders/ordersApi'
import { invoicesClient } from '@/features/invoices/invoicesApi'
import { queryKeys } from '@/platform-ui'
import { orderMoved, readMoved } from './moved'

// Own work (2026-11-25): an order's version, and its invoice's, are held by more than one page; each save reads them all again.
describe('what an order that moved reads again', () => {
  it('its detail and list, and the invoices that show or send its version', () => {
    expect(orderMoved(7)).toEqual([queryKeys.detail(ordersClient, 7), queryKeys.list(ordersClient), queryKeys.list(invoicesClient), queryKeys.details(invoicesClient)])
  })

  it('is waited for; an answered save is not read a second time', async () => {
    const qc = new QueryClient()
    const read = vi.spyOn(qc, 'invalidateQueries').mockResolvedValue(undefined)
    await readMoved(qc, 7)
    expect(read.mock.calls.map((c) => c[0]!.queryKey)).toEqual(orderMoved(7))
    read.mockClear()
    await readMoved(qc, 7, { answered: true })
    expect(read.mock.calls.map((c) => c[0]!.queryKey)).toEqual(orderMoved(7).slice(1))
  })

  it('the detail of any invoice is reached, whatever its id', async () => {
    const qc = new QueryClient()
    qc.setQueryData(queryKeys.detail(invoicesClient, 31), { version: 1 })
    qc.setQueryData(queryKeys.detail(ordersClient, 8), { version: 1 })
    await readMoved(qc, 7)
    expect(qc.getQueryState(queryKeys.detail(invoicesClient, 31))!.isInvalidated).toBe(true)
    expect(qc.getQueryState(queryKeys.detail(ordersClient, 8))!.isInvalidated).toBe(false)  // another order is not
  })

  it('is the one list: no feature names its own keys of an order or an invoice to read again', () => {
    const files = import.meta.glob('../**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
    const byHand = Object.entries(files).filter(([f, s]) => !/\.test\.|common\/moved\.ts$/.test(f)
      && /(invalidates|invalidateQueries)[^\n]*queryKeys\.(detail|details|list)\((ordersClient|invoicesClient)/.test(s)).map(([f]) => f)
    expect(Object.keys(files).length).toBeGreaterThan(50)
    expect(byHand).toEqual([])
  })
})
