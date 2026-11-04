import { noticeIn, openTab } from '@/platform-ui/testing'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import { MemoryRouter } from 'react-router-dom'
import type { ConnectionRead } from './connectionsApi'

const conn: ConnectionRead = {
  id: 7, name: 'radiant', kind: 'SFTP', host: 'sftp-test', port: 22, username: 'radiant', inbound_folder: '/in',
  outbound_folder: null, processed_folder: '/processed', error_folder: '/error', test_inbound_folder: null,
  test_outbound_folder: null, after_read: 'LEAVE', stable_seconds: 60, mode: 'TEST', secret_name: 'SALES_SECRET_SFTP_TEST',
  secret_present: true, host_key: null, auth_failed_at: null, auth_failed_reason: null, active: true, version: 3,
  api_version: null, cursor_updated_at: null, page_size: null, order_number_cutoff: null, partner_code: null,
  created_at: '2026-10-29T12:00:00Z', created_by_name: 'Op', updated_at: null, updated_by_name: null,
  schedule_minutes: 5, schedule_paused: false, active_from: null, active_to: null, active_days: null,
  last_run_at: null, last_run_status: null, last_run_summary: null, last_error: null, next_run_at: null,
}
const LINE = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample'

const api = vi.hoisted(() => ({
  test: vi.fn(), pin: vi.fn(), clear: vi.fn(), importCsv: vi.fn(),
  edi: vi.fn(async () => ({ connection_id: null, ack_timeout_minutes: 120 })), saveEdi: vi.fn(),
  shadow: vi.fn(async () => ({ shadow_mode: true })), saveShadow: vi.fn(),
  runNow: vi.fn(), runs: vi.fn(async (): Promise<{ items: unknown[]; total: number }> => ({ items: [], total: 0 })), update: vi.fn(),
  list: vi.fn(), getRun: vi.fn(),
  meta: vi.fn(async () => ({ version: 1, searchable: true, default_sort: [], fields: [], views: [], scheduler_enabled: true })),
}))
vi.mock('./connectionsApi', async (orig) => {
  const real = await orig<typeof import('./connectionsApi')>()
  return {
    ...real,
    connectionsClient: { ...real.connectionsClient, list: api.list, importCsv: api.importCsv, update: api.update, meta: api.meta },
    runConnectionNow: api.runNow,
    fetchConnectionAudit: vi.fn(async () => []),
    testConnection: api.test, pinHostKey: api.pin, clearAuthLatch: api.clear,
    fetchEdiSettings: api.edi, saveEdiSettings: api.saveEdi,
    fetchShadowSetting: api.shadow, saveShadowSetting: api.saveShadow,
  }
})
vi.mock('@/features/operations/operationsApi', async (orig) => {
  const real = await orig<typeof import('@/features/operations/operationsApi')>()
  return { ...real, runsClient: { ...real.runsClient, list: api.runs, get: api.getRun } }
})
const { default: ConnectionsPage } = await import('./ConnectionsPage')

function renderAs(keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter><QueryClientProvider client={qc}><ToastProvider>
      <PermissionProvider can={(k) => keys.includes(k)}><ConnectionsPage /></PermissionProvider>
    </ToastProvider></QueryClientProvider></MemoryRouter>,
  )
}

async function open() {
  fireEvent.click(await screen.findByText('sftp-test'))
  return within(await screen.findByRole('dialog')).findByRole('region', { name: 'Connection status' })
}

beforeEach(() => { api.list.mockResolvedValue({ items: [conn], total: 1 }) })

/** The open connection's "Schedule & runs" tab. */
async function scheduleOf() {
  const sheet = await screen.findByRole('dialog')
  await openTab(sheet, 'Schedule')
  return within(sheet).findByRole('region', { name: 'Schedule' })
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
    expect(await within(panel).findByText(/On: Zoho is live/)).toBeInTheDocument()
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

describe("a connection's schedule", () => {
  const ran = { ...conn, schedule_minutes: 5, active_from: '06:00:00', active_to: '22:00:00', active_days: 'Mon,Tue,Wed,Thu,Fri',
    last_run_at: '2026-11-02T20:00:00Z', last_run_status: 'PARTIAL', last_run_summary: '3 files, 1 errors', next_run_at: '2026-11-02T20:05:00Z' }

  it('the list shows the schedule, the last run with its summary, and the next run', async () => {
    api.list.mockResolvedValue({ items: [{ ...ran, schedule_paused: true }], total: 1 })
    renderAs(['sales.read'])
    expect(await screen.findByText('every 5 min, Mon–Fri 06:00–22:00')).toBeInTheDocument()
    expect(screen.getByText('Paused')).toBeInTheDocument()
    expect(screen.getByText('3 files, 1 errors')).toBeInTheDocument()
    expect(screen.getByText('PARTIAL')).toBeInTheDocument()
  })

  it('the form edits it: minutes, pause, hours as HH:MM and days — sent as the API takes them', async () => {
    api.list.mockResolvedValue({ items: [ran], total: 1 })
    api.update.mockResolvedValue({ ...ran, version: 4 })
    renderAs(['sales.read', 'sales.config'])
    fireEvent.click(await screen.findByText('sftp-test'))
    const sheet = await screen.findByRole('dialog')
    await openTab(sheet, 'Settings')  // the form (Overview comes first)
    expect(within(sheet).getByLabelText(/^Active from/)).toHaveValue('06:00')
    fireEvent.change(within(sheet).getByLabelText(/^Schedule: every/), { target: { value: '15' } })
    fireEvent.change(within(sheet).getByLabelText(/^Active until/), { target: { value: '' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    expect(await within(sheet).findByText('Give both the start and the end, or neither')).toBeInTheDocument()
    fireEvent.change(within(sheet).getByLabelText(/^Active until/), { target: { value: '23:30' } })
    fireEvent.click(within(within(sheet).getByRole('group', { name: 'Active days' })).getByRole('checkbox', { name: 'Sat' }))
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(7, expect.objectContaining({
      schedule_minutes: 15, active_to: '23:30:00', active_days: 'Mon,Tue,Wed,Thu,Fri,Sat' }), 3))
  })

  it('Run now needs sales.file.retry, runs this connection and shows its result when it ends', async () => {
    api.list.mockResolvedValue({ items: [ran], total: 1 })
    const { unmount } = renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('sftp-test'))
    const read = await scheduleOf()
    expect(within(read).queryByRole('button', { name: 'Run now' })).toBeNull()
    unmount()
    api.runNow.mockResolvedValue({ run_id: 31 })
    const running = { id: 31, job: 'sales_connection', scope: 'connection:7', trigger: 'MANUAL', status: 'RUNNING',
      started_at: '2026-11-02T20:10:00Z', heartbeat_at: '2026-11-02T20:10:00Z', finished_at: null, error: null, summary: null }
    api.runs.mockResolvedValueOnce({ items: [], total: 0 }).mockResolvedValueOnce({ items: [running], total: 1 })
      .mockResolvedValue({ items: [{ ...running, status: 'SUCCEEDED', finished_at: '2026-11-02T20:10:03Z',
        summary: { text: '2 files, 0 errors' } }], total: 1 })
    api.getRun.mockReset()
    api.getRun.mockResolvedValueOnce(running).mockResolvedValue({ ...running, status: 'SUCCEEDED', finished_at: '2026-11-02T20:10:03Z',
      summary: { text: '2 files, 0 errors' } })
    renderAs(['sales.read', 'sales.retry', 'sales.file.retry'])
    fireEvent.click(await screen.findByText('sftp-test'))
    const sched = await scheduleOf()
    expect(within(sched).getByText('every 5 min, Mon–Fri 06:00–22:00')).toBeInTheDocument()
    fireEvent.click(within(sched).getByRole('button', { name: 'Run now' }))
    const sheet = screen.getByRole('dialog')
    await waitFor(() => expect(noticeIn(sheet)).toHaveTextContent('Run 31 is running…'))  // the sheet's status row
    expect(api.runNow).toHaveBeenCalledWith(7)
    await waitFor(() => expect(noticeIn(sheet)).toHaveTextContent('Run 31: 2 files, 0 errors'), { timeout: 6000 })
    expect(api.runs).toHaveBeenCalledWith(expect.objectContaining({ filters: [{ field: 'scope', op: 'eq', value: 'connection:7' }] }))
  })
})

describe('the row icons on Connections', () => {
  const row = (c: ConnectionRead) => screen.getByText(c.host!).closest('tr')!

  it('shows only what the permissions allow; an inactive connection has no Run now', async () => {
    api.list.mockResolvedValue({ items: [conn, { ...conn, id: 8, host: 'old-host', active: false }], total: 2 })
    const { unmount } = renderAs(['sales.read'])
    await screen.findByText('sftp-test')
    expect(within(row(conn)).queryAllByRole('button')).toEqual([])
    unmount()
    renderAs(['sales.read', 'sales.config'])
    await screen.findByText('sftp-test')
    expect(within(row(conn)).getByRole('button', { name: 'Pause schedule' })).toBeInTheDocument()
    expect(within(row(conn)).queryByRole('button', { name: 'Run now' })).toBeNull()
    cleanup()
    renderAs(['sales.read', 'sales.file.retry'])
    await screen.findByText('sftp-test')
    expect(within(row(conn)).getByRole('button', { name: 'Run now' })).toBeInTheDocument()
    expect(within(row(conn)).queryByRole('button', { name: 'Pause schedule' })).toBeNull()
    expect(within(screen.getByText('old-host').closest('tr')!).queryByRole('button', { name: 'Run now' })).toBeNull()
  })

  it('Pause sends the versioned update without opening the row; Resume outside the window asks first', async () => {
    api.list.mockResolvedValue({ items: [conn], total: 1 })
    api.update.mockResolvedValue({ ...conn, schedule_paused: true, version: 4 })
    const { unmount } = renderAs(['sales.read', 'sales.config'])
    await screen.findByText('sftp-test')
    fireEvent.click(within(row(conn)).getByRole('button', { name: 'Pause schedule' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(7, { schedule_paused: true }, 3))
    expect(screen.queryByRole('dialog', { name: /connection/ })).toBeNull()  // the row did not open
    expect(await screen.findByText('radiant: schedule paused')).toBeInTheDocument()
    unmount()
    api.update.mockClear()
    // paused, and its window is one minute a day: resuming asks first
    const closed = { ...conn, schedule_paused: true, active_from: '03:00:00', active_to: '03:01:00' }
    api.list.mockResolvedValue({ items: [closed], total: 1 })
    renderAs(['sales.read', 'sales.config'])
    await screen.findByText('sftp-test')
    fireEvent.click(within(row(closed)).getByRole('button', { name: 'Resume schedule' }))
    const ask = await screen.findByRole('dialog', { name: 'Resume radiant?' })
    expect(api.update).not.toHaveBeenCalled()
    fireEvent.click(within(ask).getByRole('button', { name: 'Resume' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(7, { schedule_paused: false }, 3))
  })

  it('Run now is followed to its end, blocked meanwhile, and its summary toasted', async () => {
    api.list.mockResolvedValue({ items: [conn], total: 1 })
    api.runNow.mockReset()
    api.runNow.mockResolvedValue({ run_id: 12 })
    const run = { id: 12, job: 'sales_connection', scope: 'connection:7', trigger: 'MANUAL', started_at: '2026-11-03T10:00:00Z',
      heartbeat_at: '2026-11-03T10:00:00Z', finished_at: null, error: null, summary: null }
    let release: () => void = () => {}
    const finished = new Promise<void>((r) => { release = r })
    api.getRun.mockImplementation(async () => {
      await finished
      return { ...run, status: 'SUCCEEDED', finished_at: '2026-11-03T10:00:03Z', summary: { text: '3 files, 0 errors' } }
    })
    renderAs(['sales.read', 'sales.file.retry'])
    await screen.findByText('sftp-test')
    const go = within(row(conn)).getByRole('button', { name: 'Run now' })
    fireEvent.click(go)
    await waitFor(() => expect(go).toBeDisabled())
    expect(api.runNow).toHaveBeenCalledWith(7)
    fireEvent.click(go)
    expect(api.runNow).toHaveBeenCalledOnce()
    release()
    expect(await screen.findByText('Run 12: 3 files, 0 errors')).toBeInTheDocument()
    await waitFor(() => expect(go).toBeEnabled())
  })

  it('a run refused because it is already running says so', async () => {
    api.list.mockResolvedValue({ items: [conn], total: 1 })
    api.runNow.mockRejectedValue(Object.assign(new Error('running'), { body: { error: { code: 'run.already_running', message: 'radiant is running' } } }))
    renderAs(['sales.read', 'sales.file.retry'])
    await screen.findByText('sftp-test')
    fireEvent.click(within(row(conn)).getByRole('button', { name: 'Run now' }))
    expect(await screen.findByText('radiant is running; wait for it to finish.')).toBeInTheDocument()
  })
})


describe('when nothing runs by itself, and a run that is overdue', () => {
  const ran = { ...conn, schedule_minutes: 5, next_run_at: '2026-11-02T20:05:00Z' }
  const offMeta = { version: 1, searchable: true, default_sort: [], fields: [], views: [], scheduler_enabled: false }

  it('the scheduler off: a banner, and Next run says to use Run now (in the list and the Schedule tab)', async () => {
    api.meta.mockResolvedValueOnce(offMeta)
    api.list.mockResolvedValue({ items: [{ ...ran, overdue: false }], total: 1 })
    renderAs(['sales.read'])
    expect(await screen.findByText(/Automatic runs are off in this environment \(SALES_SCHEDULER_ENABLED\)/)).toBeInTheDocument()
    const row = (await screen.findByText('sftp-test')).closest('tr')!
    expect(within(row).getByText('Automatic runs off: use Run now')).toBeInTheDocument()
    expect(within(row).queryByText(/2026|Nov/)).toBeNull()  // no next-run time that never comes
    fireEvent.click(within(row).getByText('sftp-test'))
    const sheet = await screen.findByRole('dialog')
    await openTab(sheet, 'Schedule')
    expect(within(sheet).getByText(/Automatic runs are off in this environment/)).toBeInTheDocument()
    expect(within(within(sheet).getByRole('region', { name: 'Schedule' })).getByText('Automatic runs off: use Run now')).toBeInTheDocument()
  })

  it('the scheduler on: no banner; an overdue connection is marked Overdue beside its next run', async () => {
    api.list.mockResolvedValue({ items: [{ ...ran, overdue: true }], total: 1 })
    renderAs(['sales.read'])
    const row = (await screen.findByText('sftp-test')).closest('tr')!
    await waitFor(() => expect(within(row).getByText('Overdue')).toBeInTheDocument())
    expect(screen.queryByText(/Automatic runs are off/)).toBeNull()
    expect(within(row).queryByText('Automatic runs off: use Run now')).toBeNull()
  })
})
