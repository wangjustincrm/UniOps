import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import { takeOpenRequest, takeViewRequest } from '@/platform-ui/resource/viewRequests'
import type { DashboardRead, HealthRead } from './healthApi'

const health: HealthRead = {
  result: 'FAILED', checked_at: '2026-11-02T20:00:00Z', shadow_mode: true, partner_writes_enabled: false,
  external_email_enabled: false, scheduler_enabled: false, gate_ok: false, jobs: [
    { scope: 'system', label: 'System job', connection_id: null, interval_minutes: 1, schedule: 'every 1 min', paused: false,
      last_run_at: '2026-11-02T19:59:00Z', last_run_status: 'SUCCEEDED', last_run_summary: '0 940s queued, 0 sent, 0 problems',
      next_run_at: '2026-11-02T20:00:00Z', overdue: false },
    { scope: 'connection:7', label: 'radiant', connection_id: 7, interval_minutes: 5, schedule: 'every 5 min, Mon–Fri 06:00–22:00',
      paused: false, last_run_at: '2026-11-02T19:00:00Z', last_run_status: 'FAILED', last_run_summary: 'OSError: timed out',
      next_run_at: '2026-11-02T19:05:00Z', overdue: true }],
  scheduler_paused: false, system_minutes: 1,
  cards: { critical: 1, warnings: 2, failed_inbound: 3, failed_outbound: 0, waiting_ack: 1, rejected_ack: 0, ack_timeout: 0,
    exceptions_944: 0, exceptions_945: 0, transfer_discrepancies: 0 },
  checks: [
    { key: 'settings', label: 'Required settings', result: 'PASSED', detail: 'set', count: null, link: { page: '/config/connections' } },
    { key: 'connection:7', label: 'Connection radiant', result: 'FAILED', detail: 'authentication failed', count: null,
      link: { page: '/config/connections' } },
    { key: 'failed_inbound', label: 'Failed inbound files', result: 'WARNING', detail: '3', count: 3,
      link: { page: '/documents', resource: 'documents', filters: [{ field: 'direction', op: 'eq', value: 'IN' },
        { field: 'processing_status', op: 'eq', value: 'Failed' }] } },
  ],
  gate: [{ event_type: '940_failed', label: '940 failed', rule_id: null, recipients: 0, tested_at: null, ok: false,
    problem: 'no active rule; no test notification sent' }],
}
const dashboard: DashboardRead = {
  cards: [{ key: 'on_hold', label: 'On Hold', count: 4, parts: [],
    link: { page: '/orders', resource: 'orders', filters: [{ field: 'order_state', op: 'eq', value: 'On Hold' }] } }],
  open_tasks: [{ type: 'sales_940_failed', count: 2 }], needs_review: 1, waiting_delivery: 0,
  runs: [{ id: 9, trigger: 'SCHEDULED', status: 'SUCCEEDED', started_at: '2026-11-02T19:55:00Z', finished_at: null, error: null }],
}

const h = vi.hoisted(() => ({ health: vi.fn(), dash: vi.fn(), resolve: vi.fn(), test: vi.fn(), save: vi.fn(), system: vi.fn(), getRun: vi.fn() }))
vi.mock('./healthApi', async (orig) => ({ ...(await orig<typeof import('./healthApi')>()),
  fetchHealth: h.health, fetchDashboard: h.dash, resolveInbox: h.resolve, saveScheduler: h.save }))
vi.mock('@/features/operations/operationsApi', async (orig) => ({
  ...(await orig<typeof import('@/features/operations/operationsApi')>()), runSystemNow: h.system }))
vi.mock('@/features/operations/followRun', async (orig) => ({
  ...(await orig<typeof import('@/features/operations/followRun')>()), followRun: async (id: number) => ({ ...(await h.getRun()), id }) }))
vi.mock('@/features/configuration/connections/connectionsApi', async (orig) => ({
  ...(await orig<typeof import('@/features/configuration/connections/connectionsApi')>()), testConnection: h.test }))
vi.mock('@/features/configuration/notifications/notificationsApi', async (orig) => ({
  ...(await orig<typeof import('@/features/configuration/notifications/notificationsApi')>()),
  fetchEvents: vi.fn(async () => [{ key: '940_failed', label: '940 failed', kind: 'action', gate: true, rule_id: null }]) }))

const { HomeDashboard } = await import('./HomeDashboard')
const { default: HealthPage } = await import('./HealthPage')
const { InboxRedirect } = await import('@/app/InboxRedirect')

function Where() { return <p>at {useLocation().pathname}</p> }
function renderAt(path: string, el: React.ReactNode, keys = ['sales.read', 'sales.config']) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={(k) => keys.includes(k)}>
    <MemoryRouter initialEntries={[path]}><Routes>
      <Route path={path} element={el} /><Route path="*" element={<Where />} />
    </Routes></MemoryRouter>
  </PermissionProvider></ToastProvider></QueryClientProvider>)
}

beforeEach(() => {
  Object.values(h).forEach((f) => f.mockReset())
  h.health.mockResolvedValue(health)
  h.dash.mockResolvedValue(dashboard)
})

describe('Home (FR-OB-52b)', () => {
  it('each card opens its own list; open tasks are named by their event', async () => {
    renderAt('/home', <HomeDashboard />)
    expect(await screen.findByText('940 failed')).toBeInTheDocument()  // sales_940_failed, labelled
    expect(screen.getByRole('region', { name: 'Health' })).toHaveTextContent('FAILED1 critical, 2 warnings')
    fireEvent.click(screen.getByRole('button', { name: /On Hold\s*4/ }))
    expect(await screen.findByText('at /orders')).toBeInTheDocument()
    expect(takeViewRequest('orders')).toEqual({ name: 'On Hold', state: { filters: [{ field: 'order_state', op: 'eq', value: 'On Hold' }] } })
  })
})

describe('Health (FR-OB-52a)', () => {
  it('shows the result, the switches and the gate; a check opens its records; a connection can be tested', async () => {
    h.test.mockResolvedValue({ host_key: null, folders: [], error: 'Permission denied' })
    renderAt('/health', <HealthPage />)
    const checks = await screen.findByRole('table', { name: 'Health checks' })
    expect(within(checks).getByText('authentication failed')).toBeInTheDocument()
    expect(screen.getByText('On — nothing is sent, Zoho is live')).toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Go-live gate' })).getByText('no active rule; no test notification sent')).toBeInTheDocument()
    const row = within(checks).getByText('Connection radiant').closest('tr')!
    fireEvent.click(within(row).getByRole('button', { name: 'Test' }))
    expect(await within(row).findByRole('status')).toHaveTextContent('Permission denied')
    expect(h.test).toHaveBeenCalledWith(7)
    fireEvent.click(within(row).getByRole('button', { name: 'Open' }))
    expect(await screen.findByText('at /config/connections')).toBeInTheDocument()
    expect(takeOpenRequest('/config/connections')).toBe(7)
  })

  it('the failed-inbound card opens the documents with its filters', async () => {
    renderAt('/health', <HealthPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Failed inbound\s*3/ }))
    expect(await screen.findByText('at /documents')).toBeInTheDocument()
    expect(takeViewRequest('documents')).toEqual({ name: 'Failed inbound files', state: { filters: health.checks[2].link!.filters } })
  })
})

describe('a Task Inbox link', () => {
  it('/inbox/<key> asks which record, asks its list to open it, and goes there', async () => {
    h.resolve.mockResolvedValue({ page: '/orders', resource: '/orders', id: 23 })
    renderAt('/inbox/SALES:order_review:23', <InboxRedirect taskKey="SALES:order_review:23" />)
    expect(await screen.findByText('at /orders')).toBeInTheDocument()
    expect(h.resolve).toHaveBeenCalledWith('SALES:order_review:23')
    await waitFor(() => expect(takeOpenRequest('/orders')).toBe(23))
  })
})

describe('Health → Scheduler', () => {
  it('lists every job with its last and next run, flags the overdue one, runs the system job and edits the settings', async () => {
    h.system.mockResolvedValue({ run_id: 50 })
    h.save.mockResolvedValue({ paused: true, system_minutes: 2 })
    renderAt('/health', <HealthPage />, ['sales.read', 'sales.config', 'sales.file.retry'])
    const jobs = await screen.findByRole('table', { name: 'Scheduled jobs' })
    const radiant = within(jobs).getByText('radiant').closest('tr')!
    expect(radiant).toHaveTextContent('every 5 min, Mon–Fri 06:00–22:00')
    expect(within(radiant).getByText('Overdue')).toBeInTheDocument()
    expect(within(radiant).getByText('OSError: timed out')).toBeInTheDocument()
    expect(within(within(jobs).getByText('System job').closest('tr')!).queryByText('Overdue')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Run the system job now' }))
    await waitFor(() => expect(h.system).toHaveBeenCalledOnce())
    // each job row has its own Run now icon (the system job's here), followed to a toast
    h.system.mockResolvedValue({ run_id: 51 })
    h.getRun.mockResolvedValue({ id: 51, job: 'sales_system', scope: 'system', trigger: 'MANUAL', status: 'SUCCEEDED',
      started_at: '2026-11-02T20:00:00Z', heartbeat_at: '2026-11-02T20:00:00Z', finished_at: '2026-11-02T20:00:01Z', error: null,
      summary: { text: '0 940s queued, 0 sent, 0 problems' } })
    fireEvent.click(within(within(jobs).getByText('System job').closest('tr')!).getByRole('button', { name: 'Run now: System job' }))
    expect(await screen.findByText('Run 51: 0 940s queued, 0 sent, 0 problems')).toBeInTheDocument()
    expect(within(radiant).getByRole('button', { name: 'Run now: radiant' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Scheduler settings' }))
    const sheet = await screen.findByRole('dialog', { name: 'Scheduler settings' })
    fireEvent.click(within(sheet).getByRole('checkbox', { name: /Pause every job/ }))
    fireEvent.change(within(sheet).getByLabelText(/^System job: every/), { target: { value: '2' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(h.save).toHaveBeenCalledWith({ paused: true, system_minutes: 2 }))
  })

  it('without sales.config there is no settings button', async () => {
    renderAt('/health', <HealthPage />, ['sales.read'])
    await screen.findByRole('table', { name: 'Scheduled jobs' })
    expect(screen.queryByRole('button', { name: 'Scheduler settings' })).toBeNull()
  })
})

