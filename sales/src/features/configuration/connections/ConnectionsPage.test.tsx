import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { ConnectionRead } from './connectionsApi'

const conn: ConnectionRead = {
  id: 7, name: 'radiant', kind: 'SFTP', host: 'sftp-test', port: 22, username: 'radiant', inbound_folder: '/in',
  outbound_folder: null, processed_folder: '/processed', error_folder: '/error', test_inbound_folder: null,
  test_outbound_folder: null, after_read: 'LEAVE', stable_seconds: 60, mode: 'TEST', secret_name: 'SALES_SECRET_SFTP_TEST',
  secret_present: true, host_key: null, auth_failed_at: null, auth_failed_reason: null, active: true, version: 3,
  api_version: null, cursor_updated_at: null, page_size: null, order_number_cutoff: null, partner_code: null,
  created_at: '2026-10-29T12:00:00Z', created_by_name: 'Op', updated_at: null, updated_by_name: null,
}
const LINE = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample'

const api = vi.hoisted(() => ({
  test: vi.fn(), pin: vi.fn(), clear: vi.fn(), importCsv: vi.fn(),
  edi: vi.fn(async () => ({ connection_id: null, ack_timeout_minutes: 120 })), saveEdi: vi.fn(),
  shadow: vi.fn(async () => ({ shadow_mode: true })), saveShadow: vi.fn(),
}))
vi.mock('./connectionsApi', async (orig) => {
  const real = await orig<typeof import('./connectionsApi')>()
  return {
    ...real,
    connectionsClient: { ...real.connectionsClient, list: vi.fn(async () => ({ items: [conn], total: 1 })), importCsv: api.importCsv },
    fetchConnectionAudit: vi.fn(async () => []),
    testConnection: api.test, pinHostKey: api.pin, clearAuthLatch: api.clear,
    fetchEdiSettings: api.edi, saveEdiSettings: api.saveEdi,
    fetchShadowSetting: api.shadow, saveShadowSetting: api.saveShadow,
  }
})
const { default: ConnectionsPage } = await import('./ConnectionsPage')

function renderAs(keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}><ToastProvider>
      <PermissionProvider can={(k) => keys.includes(k)}><ConnectionsPage /></PermissionProvider>
    </ToastProvider></QueryClientProvider>,
  )
}

async function open() {
  fireEvent.click(await screen.findByText('sftp-test'))
  return within(await screen.findByRole('dialog')).findByRole('region', { name: 'Connection status' })
}

describe('Connections page', () => {
  it('a reader sees the status but cannot test, pin or clear', async () => {
    renderAs(['sales.read'])
    expect(await screen.findByText('Key not pinned')).toBeInTheDocument()  // the list's state column
    const status = await open()
    expect(within(status).getByText('present')).toBeInTheDocument()
    expect(within(status).getByText('not pinned')).toBeInTheDocument()
    expect(within(status).queryByText('Test connection')).not.toBeInTheDocument()
  })

  it('with sales.config: the test shows the fingerprint and pins the key', async () => {
    api.test.mockResolvedValue({ host_key: { line: LINE, fingerprint: 'SHA256:abc', pinned: false, matches: false },
      folders: [], error: 'the host key is not pinned yet' })
    api.pin.mockResolvedValue({ ...conn, host_key: LINE, version: 4 })
    renderAs(['sales.read', 'sales.config'])
    const status = await open()
    fireEvent.click(within(status).getByText('Test connection'))
    expect(await within(status).findByText('SHA256:abc')).toBeInTheDocument()
    fireEvent.click(within(status).getByText('Pin this key'))
    await waitFor(() => expect(api.pin).toHaveBeenCalledWith(expect.objectContaining({ id: 7, version: 3 }), LINE))
  })
})

describe('CSV import preview (2026-10-29, the dev2 Connections file)', () => {
  it('shows every refused row with its field and message, and nothing can be imported', async () => {
    const HOST_KEY = "host_key must be the server's public key line (e.g. 'ssh-ed25519 AAAA…'). Leave it empty and use Test connection → Pin this key. Passwords and tokens never go in the CSV; put them in the server environment under secret_name."
    const SECRET = 'secret_name must be an environment variable name like SALES_SECRET_RADIANT (capitals, digits, underscore)'
    const EXCEL = 'this looks like Excel changed a number; format the column as Text'
    api.importCsv.mockResolvedValue({ ok: false, dry_run: true, written: false, rows: [], counts: { create: 0, update: 0, unchanged: 0 },
      errors: [
        { row: 1, column: 'host_key', code: 'csv.invalid', message: HOST_KEY },
        { row: 2, column: 'username', code: 'csv.invalid', message: EXCEL },
        { row: 3, column: 'secret_name', code: 'csv.invalid', message: SECRET },
      ] })
    renderAs(['sales.read', 'sales.config'])
    fireEvent.change(await screen.findByLabelText('CSV file'), { target: { files: [new File(['x'], 'connections.csv')] } })
    const dialog = await screen.findByRole('dialog')
    expect(api.importCsv).toHaveBeenCalledWith(expect.any(File), true)  // a dry run first
    expect(within(dialog).getByText(/Nothing will be written/)).toBeInTheDocument()
    expect(within(dialog).getByText(`Row 1 · host_key: ${HOST_KEY}`)).toBeInTheDocument()
    expect(within(dialog).getByText(`Row 2 · username: ${EXCEL}`)).toBeInTheDocument()
    expect(within(dialog).getByText(`Row 3 · secret_name: ${SECRET}`)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Import' })).toBeDisabled()
  })
})

describe('a Shopify connection', () => {
  it('has no SSH host key or pin; shows the cursor read-only with a reset for sales.config', async () => {
    const mod = await import('./connectionsApi')
    const shop = { ...conn, id: 2, name: 'Shopify', kind: 'SHOPIFY' as const, host: 'example.myshopify.com', port: 443,
      username: null, inbound_folder: null, secret_name: 'SALES_SECRET_SHOPIFY', host_key: null, api_version: '2026-04',
      partner_code: 'SHOP', cursor_updated_at: '2026-10-28T12:00:00Z' }
    vi.mocked(mod.connectionsClient.list).mockResolvedValue({ items: [shop], total: 1 })
    const { unmount } = renderAs(['sales.read'])
    expect(await screen.findByText('Ready')).toBeInTheDocument()  // not "Key not pinned"
    fireEvent.click(await screen.findByText('example.myshopify.com'))
    let status = await within(await screen.findByRole('dialog')).findByRole('region', { name: 'Connection status' })
    expect(within(status).getByText(/Next pull from/)).toBeInTheDocument()
    expect(within(status).queryByText('Reset cursor')).not.toBeInTheDocument()  // a reader cannot
    expect(within(status).queryByText(/Host key/)).not.toBeInTheDocument()
    unmount()
    renderAs(['sales.read', 'sales.config'])
    fireEvent.click(await screen.findByText('example.myshopify.com'))
    status = await within(await screen.findByRole('dialog')).findByRole('region', { name: 'Connection status' })
    expect(within(status).getByText('Reset cursor')).toBeDisabled()  // until a date is chosen
    expect(within(status).getByText(/Reads the store's name/)).toBeInTheDocument()
  })
})

describe('Connections page: warehouse EDI settings (P4)', () => {
  it('a reader sees them read-only; sales.config saves the connection and the 997 wait', async () => {
    const { unmount } = renderAs(['sales.read'])
    let panel = await screen.findByRole('group', { name: 'Warehouse EDI' })
    expect(within(panel).getByText('120 minutes')).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: 'Edit' })).toBeNull()  // read only
    unmount()
    api.saveEdi.mockImplementation(async (d) => d)
    renderAs(['sales.read', 'sales.config'])
    panel = await screen.findByRole('group', { name: 'Warehouse EDI' })
    fireEvent.click(within(panel).getByRole('button', { name: 'Edit' }))
    const sheet = await screen.findByRole('dialog', { name: 'Warehouse EDI and 810 invoices' })
    fireEvent.change(within(sheet).getByLabelText(/997 wait \(minutes\)/), { target: { value: '90' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.saveEdi).toHaveBeenCalledWith({ connection_id: null, ack_timeout_minutes: 90, invoice_connection_id: null }))
  })
})

describe('Connections page: the shadow run switch', () => {
  it('says what it does; only sales.config turns it off, after a confirmation', async () => {
    const { unmount } = renderAs(['sales.read'])
    let panel = await screen.findByRole('region', { name: 'Shadow run' })
    expect(await within(panel).findByRole('status')).toHaveTextContent(/On: Zoho is live/)
    expect(within(panel).queryByText('Turn the shadow run off')).toBeNull()
    unmount()
    api.saveShadow.mockImplementation(async (d) => d)
    renderAs(['sales.read', 'sales.config'])
    panel = await screen.findByRole('region', { name: 'Shadow run' })
    fireEvent.click(await within(panel).findByText('Turn the shadow run off'))
    expect(api.saveShadow).not.toHaveBeenCalled()
    const confirm = await screen.findByRole('dialog', { name: 'Turn the shadow run off?' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Turn off' }))
    await waitFor(() => expect(api.saveShadow).toHaveBeenCalledWith({ shadow_mode: false }))
  })
})

describe('Connections page: switching the shadow run on', () => {
  it('says how many open orders became Zoho\'s', async () => {
    api.shadow.mockResolvedValueOnce({ shadow_mode: false })
    api.saveShadow.mockResolvedValue({ shadow_mode: true, marked_orders: 26 })
    renderAs(['sales.read', 'sales.config'])
    const panel = await screen.findByRole('region', { name: 'Shadow run' })
    fireEvent.click(await within(panel).findByText('Turn the shadow run on'))
    expect(await within(panel).findByText("26 open order(s) were marked as Zoho's.")).toBeInTheDocument()
  })
})
