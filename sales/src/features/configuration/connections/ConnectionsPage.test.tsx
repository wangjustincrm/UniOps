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
  created_at: '2026-10-29T12:00:00Z', created_by_name: 'Op', updated_at: null, updated_by_name: null,
}
const LINE = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample'

const api = vi.hoisted(() => ({
  test: vi.fn(), pin: vi.fn(), clear: vi.fn(), importCsv: vi.fn(),
}))
vi.mock('./connectionsApi', async (orig) => {
  const real = await orig<typeof import('./connectionsApi')>()
  return {
    ...real,
    connectionsClient: { ...real.connectionsClient, list: vi.fn(async () => ({ items: [conn], total: 1 })), importCsv: api.importCsv },
    fetchConnectionAudit: vi.fn(async () => []),
    testConnection: api.test, pinHostKey: api.pin, clearAuthLatch: api.clear,
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
  it('has no SSH host key, pin or test — only its secret and the P3 note', async () => {
    const mod = await import('./connectionsApi')
    const shop = { ...conn, id: 2, name: 'Shopify', kind: 'SHOPIFY' as const, host: 'example.myshopify.com', port: 443,
      username: null, inbound_folder: null, secret_name: 'SALES_SECRET_SHOPIFY', host_key: null }
    vi.mocked(mod.connectionsClient.list).mockResolvedValue({ items: [shop], total: 1 })
    renderAs(['sales.read', 'sales.config'])
    expect(await screen.findByText('Ready')).toBeInTheDocument()  // not "Key not pinned"
    fireEvent.click(await screen.findByText('example.myshopify.com'))
    const status = await within(await screen.findByRole('dialog')).findByRole('region', { name: 'Connection status' })
    expect(within(status).getByText(/Shopify connection test arrives with the Shopify gateway \(P3\)/)).toBeInTheDocument()
    expect(within(status).queryByText('Test connection')).not.toBeInTheDocument()
    expect(within(status).queryByText(/Host key/)).not.toBeInTheDocument()
  })
})
