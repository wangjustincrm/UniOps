import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'

const meta = { version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }
const LOG = { id: 1, uuid: 'a1b2c3d4-0000-0000-0000-000000000001', kind: 'request', name: null, correlation_id: 'corr-9', user_name: 'Op',
  role: 'op', client_ip: '1.2.3.4', http_method: 'POST', route: '/api/v1/orders/{id}/settle', http_status: 409,
  execution_time: '2026-11-23T15:00:00Z', execution_duration_ms: 12, error_code: 'entity.stale', has_exception: true, entity_changes: 1 }
const api = vi.hoisted(() => ({ list: vi.fn(), changes: vi.fn(), detail: vi.fn() }))
vi.mock('./auditLogApi', async (orig) => ({
  ...(await orig<typeof import('./auditLogApi')>()),
  auditLogsClient: { basePath: '/audit-logs', meta: async () => meta, list: api.list },
  entityChangesClient: { basePath: '/entity-changes', meta: async () => meta, list: api.changes },
  fetchAuditLog: api.detail,
}))
const { default: AuditLogPage } = await import('./AuditLogPage')

function page() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={() => true}>
    <AuditLogPage /></PermissionProvider></ToastProvider></QueryClientProvider>)
}

describe('Operations → Audit log (sales36)', () => {
  it('lists the logs; a row opens its Overall, Actions and Entity changes', async () => {
    api.list.mockResolvedValue({ items: [LOG], total: 1 })
    api.changes.mockResolvedValue({ items: [], total: 0 })
    api.detail.mockResolvedValue({ ...LOG, id: LOG.uuid, exceptions: 'entity.stale: someone changed this order',
      actions: [{ id: 1, service_name: 'OrderPaymentService', method_name: 'settle', parameters: { reference: 'EFT 1' }, execution_time: LOG.execution_time }],
      changes: [{ id: 5, at: LOG.execution_time, user_name: 'Op', action: 'update', entity: 'sales_orders', entity_id: '7',
        before: { settled_on: null, version: 2 }, after: { settled_on: '2026-11-20', version: 3 }, reason: 'settled' }] })
    page()
    fireEvent.click(await screen.findByText('POST /api/v1/orders/{id}/settle'))
    const sheet = await screen.findByRole('dialog')
    await waitFor(() => expect(api.detail).toHaveBeenCalledWith(LOG.uuid))
    expect(await within(sheet).findByText('entity.stale: someone changed this order')).toBeInTheDocument()
    fireEvent.mouseDown(within(sheet).getByRole('tab', { name: /Actions/ }))
    fireEvent.click(within(sheet).getByRole('tab', { name: /Actions/ }))
    expect(await within(sheet).findByText('OrderPaymentService.settle')).toBeInTheDocument()
  })

  it('the quick filters become the list\'s filters', async () => {
    api.list.mockResolvedValue({ items: [], total: 0 })
    page()
    await screen.findByText('Nothing recorded yet')
    fireEvent.click(screen.getByRole('button', { name: 'Outcome' }))
    fireEvent.click(await screen.findByText('Failed (4xx / 5xx)'))
    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({
      filters: expect.arrayContaining([expect.objectContaining({ field: 'http_status', op: 'gte', value: '400' })]) })))
  })
})
