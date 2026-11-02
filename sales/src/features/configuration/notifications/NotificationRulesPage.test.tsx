import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import { chooseOption } from '@/platform-ui/testing'
import type { NotificationRuleRead } from './notificationsApi'

const rule: NotificationRuleRead = {
  id: 3, event_type: 'cancel_after_940', event_label: 'Cancelled at source after the 940', kind: 'action', gate: true,
  channel: 'BOTH', users: ['u-ann'], emails: ['ops@radiant.example'], active: true, tested_at: null, test_result: null,
  version: 1, created_at: '2026-11-02T12:00:00Z', updated_at: null,
}
const api = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), test: vi.fn() }))
vi.mock('./notificationsApi', async (orig) => {
  const real = await orig<typeof import('./notificationsApi')>()
  return { ...real, rulesClient: { ...real.rulesClient, list: api.list, create: api.create },
    fetchEvents: vi.fn(async () => [
      { key: 'cancel_after_940', label: 'Cancelled at source after the 940', kind: 'action', gate: true, rule_id: 3 },
      { key: '940_failed', label: '940 failed', kind: 'action', gate: true, rule_id: null }]),
    fetchUsers: vi.fn(async () => [{ id: 'u-ann', name: 'Ann Owner', email: 'ann@x.example' }, { id: 'u-bob', name: 'Bob', email: 'bob@x.example' }]),
    sendTest: api.test, fetchRuleAudit: vi.fn(async () => []) }
})
const { default: NotificationRulesPage } = await import('./NotificationRulesPage')

function renderAs(keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={(k) => keys.includes(k)}>
    <NotificationRulesPage /></PermissionProvider></ToastProvider></QueryClientProvider>)
}

describe('Configuration → Notifications (FR-OB-56)', () => {
  it('a rule: the event (only those without one), the channel, users and addresses — sent as lists', async () => {
    api.list.mockResolvedValue({ items: [rule], total: 1 })
    api.create.mockResolvedValue({ ...rule, id: 4, event_type: '940_failed' })
    renderAs(['sales.read', 'sales.config'])
    expect(await screen.findByText('Cancelled at source after the 940')).toBeInTheDocument()
    expect(screen.getByText('1 user · ops@radiant.example')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'New' }))
    const sheet = await screen.findByRole('dialog', { name: 'New notification rule' })
    fireEvent.keyDown(within(sheet).getByRole('combobox', { name: /^Event/ }), { key: 'Enter' })
    expect((await screen.findAllByRole('option')).map((o) => o.textContent)).toEqual(['—', '940 failed'])  // the ruled one is not offered
    fireEvent.click(screen.getByRole('option', { name: '940 failed' }))
    await chooseOption(within(sheet).getByRole('combobox', { name: /^Channel/ }), 'Task and email')
    fireEvent.click(await within(sheet).findByRole('checkbox', { name: 'Bob (bob@x.example)' }))
    fireEvent.change(within(sheet).getByLabelText(/^Other email addresses/), { target: { value: 'qa@x.example\nnot-an-address' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    expect(await within(sheet).findByText('Not an email address')).toBeInTheDocument()
    fireEvent.change(within(sheet).getByLabelText(/^Other email addresses/), { target: { value: 'qa@x.example, wh@x.example' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledWith({ event_type: '940_failed', channel: 'BOTH', users: ['u-bob'],
      emails: ['qa@x.example', 'wh@x.example'], active: true }))
  })

  it('a rule is tested from its sheet with sales.config only', async () => {
    api.list.mockResolvedValue({ items: [rule], total: 1 })
    api.test.mockResolvedValue({ rule: { ...rule, tested_at: '2026-11-02T20:00:00Z' }, result: 'task written; email queued to 2 address(es) (outbox #5)' })
    const { unmount } = renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('Cancelled at source after the 940'))
    expect(within(await screen.findByRole('dialog')).queryByRole('button', { name: 'Send test notification' })).toBeNull()
    unmount()
    renderAs(['sales.read', 'sales.config'])
    fireEvent.click(await screen.findByText('Cancelled at source after the 940'))
    const sheet = await screen.findByRole('dialog')
    fireEvent.click(within(sheet).getByRole('button', { name: 'Send test notification' }))
    expect(await within(sheet).findByText('task written; email queued to 2 address(es) (outbox #5)')).toBeInTheDocument()
    expect(api.test).toHaveBeenCalledWith(3)
  })
})
