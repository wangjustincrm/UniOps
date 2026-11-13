import { noticeIn, openTab } from '@/platform-ui/testing'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import { MemoryRouter } from 'react-router-dom'
import type { ConnectionRead } from './connectionsApi'

const conn: ConnectionRead = {
  id: 7, name: 'radiant', kind: 'SFTP', host: 'sftp-test', port: 22, username: 'radiant', inbound_folder: '/in',
  outbound_folder: null, processed_folder: '/processed', error_folder: '/error',
  after_read: 'LEAVE', stable_seconds: 60, can_read: true, can_write: false, secret_name: 'SALES_SECRET_SFTP_TEST',
  secret_present: true, host_key: null, auth_failed_at: null, auth_failed_reason: null, active: true, version: 3,
  api_version: null, cursor_updated_at: null, page_size: null, order_number_cutoff: null, partner_code: null,
  created_at: '2026-10-29T12:00:00Z', created_by_name: 'Op', updated_at: null, updated_by_name: null,
  schedule_minutes: 5, schedule_paused: false, active_from: null, active_to: null, active_days: null,
  last_run_at: null, last_run_status: null, last_run_summary: null, last_error: null, next_run_at: null,
}
const LINE = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample'

const api = vi.hoisted(() => ({
  test: vi.fn(), pin: vi.fn(), clear: vi.fn(), importCsv: vi.fn(),
  edi: vi.fn(async (): Promise<{ connection_id: number | null; ack_timeout_minutes: number }> => ({ connection_id: null, ack_timeout_minutes: 120 })), saveEdi: vi.fn(),
  scheduler: vi.fn(async () => ({ paused: false, system_minutes: 5, enabled: true })), saveScheduler: vi.fn(), create: vi.fn(), get: vi.fn(),
  runNow: vi.fn(), runs: vi.fn(async (): Promise<{ items: unknown[]; total: number }> => ({ items: [], total: 0 })), update: vi.fn(),
  list: vi.fn(), getRun: vi.fn(),
  meta: vi.fn(async () => ({ version: 1, searchable: true, default_sort: [], fields: [], views: [] })),
}))
vi.mock('./connectionsApi', async (orig) => {
  const real = await orig<typeof import('./connectionsApi')>()
  return {
    ...real,
    connectionsClient: { ...real.connectionsClient, list: api.list, importCsv: api.importCsv, update: api.update, meta: api.meta, create: api.create, get: api.get },
    fetchSchedulerSettings: api.scheduler, saveSchedulerSettings: api.saveScheduler,
    runConnectionNow: api.runNow,
    fetchConnectionAudit: vi.fn(async () => []),
    testConnection: api.test, pinHostKey: api.pin, clearAuthLatch: api.clear,
    fetchEdiSettings: api.edi, saveEdiSettings: api.saveEdi,
  }
})
vi.mock('@/features/operations/operationsApi', async (orig) => {
  const real = await orig<typeof import('@/features/operations/operationsApi')>()
  return { ...real, runsClient: { ...real.runsClient, list: api.runs, get: api.getRun } }
})
const { default: ConnectionsPage } = await import('./ConnectionsPage')
const { connectionFields } = await import('./connectionSchema')

function renderAs(keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter><QueryClientProvider client={qc}><ToastProvider>
      <PermissionProvider can={(k) => keys.includes(k)}><ConnectionsPage /></PermissionProvider>
    </ToastProvider></QueryClientProvider></MemoryRouter>,
  )
}

const step = (status: HTMLElement, name: string) => within(status).getByRole('listitem', { name })
// One column's cell in a row (the Write column also says Off: a row-wide search would find it).
const cellOf = (row: HTMLElement, header: string) => {
  const i = screen.getAllByRole('columnheader').findIndex((h) => (h.textContent ?? '').trim().startsWith(header))
  return within(row).getAllByRole('cell')[i]
}

async function open() {
  fireEvent.click(await screen.findByText('sftp-test'))
  return within(await screen.findByRole('dialog')).findByRole('region', { name: 'Connection status' })
}

beforeEach(() => {
  api.list.mockResolvedValue({ items: [conn], total: 1 })
  api.edi.mockResolvedValue({ connection_id: null, ack_timeout_minutes: 120 })
  api.scheduler.mockResolvedValue({ paused: false, system_minutes: 5, enabled: true })
})

/** A summary card, once its value has come. */
const card = (label: string, value: string) => screen.findByRole('button', { name: new RegExp(`^${label}\\s*${value}`, 'i') })

/** The open connection's "Schedule & runs" tab. */
async function scheduleOf() {
  const sheet = await screen.findByRole('dialog')
  await openTab(sheet, 'Schedule')
  return within(sheet).findByRole('region', { name: 'Schedule' })
}

describe('Connections page', () => {
  it('a reader sees the status but cannot test, pin or clear', async () => {
    renderAs(['sales.read'])
    expect(await screen.findByText('Key not pinned')).toBeInTheDocument()  // the list's health column
    const status = await open()
    expect(step(status, 'Secret on server')).toHaveAttribute('data-status', 'done')
    expect(step(status, 'Host key pinned')).toHaveAttribute('data-status', 'todo')
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
    // the file's own column names: the person is fixing the file
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
    expect(within(status).getByText(/reads the store's name/)).toBeInTheDocument()
  })
})

describe('Connections page: warehouse EDI settings (P4)', () => {
  it('a reader sees them read-only; sales.config saves the connection and the 997 wait', async () => {
    const { unmount } = renderAs(['sales.read'])
    fireEvent.click(await card('997 wait', '120 min'))
    const read = await screen.findByRole('dialog', { name: 'Warehouse EDI, 810 invoices and tasks' })
    expect(within(read).queryByRole('button', { name: 'Save' })).toBeNull()  // read only
    unmount()
    api.saveEdi.mockImplementation(async (d) => d)
    renderAs(['sales.read', 'sales.config'])
    fireEvent.click(await card('997 wait', '120 min'))
    const sheet = await screen.findByRole('dialog', { name: 'Warehouse EDI, 810 invoices and tasks' })
    fireEvent.change(within(sheet).getByLabelText(/997 wait \(minutes\)/), { target: { value: '90' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.saveEdi).toHaveBeenCalledWith({ connection_id: null, ack_timeout_minutes: 90, invoice_connection_id: null, internal_emails: [], operator_user_id: null, express_offered: true, ltl_from_lb: 150 }))
  })
})

describe("a connection's schedule", () => {
  const ran = { ...conn, schedule_minutes: 5, active_from: '06:00:00', active_to: '22:00:00', active_days: 'Mon,Tue,Wed,Thu,Fri',
    last_run_at: '2026-11-02T20:00:00Z', last_run_status: 'PARTIAL', last_run_summary: '3 files, 1 errors', next_run_at: '2026-11-02T20:05:00Z' }

  it('the list shows the schedule, the last run with its summary, and the next run', async () => {
    api.list.mockResolvedValue({ items: [{ ...ran, schedule_paused: true }], total: 1 })
    renderAs(['sales.read'])
    expect(await screen.findByText('Paused')).toBeInTheDocument()
    expect(screen.getByText('3 files, 1 errors')).toBeInTheDocument()
    expect(screen.getByText('PARTIAL')).toBeInTheDocument()
  })

  it('a running schedule reads as a phrase', async () => {
    api.list.mockResolvedValue({ items: [ran], total: 1 })
    renderAs(['sales.read'])
    expect(await screen.findByText('Every 5 min · Mon–Fri 06:00–22:00')).toBeInTheDocument()
  })

  it('the form edits it: minutes, pause, hours as HH:MM and days — sent as the API takes them', async () => {
    api.list.mockResolvedValue({ items: [ran], total: 1 })
    api.update.mockResolvedValue({ ...ran, version: 4 })
    renderAs(['sales.read', 'sales.config'])
    fireEvent.click(await screen.findByText('sftp-test'))
    const sheet = await screen.findByRole('dialog')
    await openTab(sheet, 'Settings')  // the schedule is not here any more
    expect(within(sheet).queryByLabelText(/^From/)).toBeNull()
    expect(within(sheet).queryByRole('radio', { name: '15 min' })).toBeNull()
    await openTab(sheet, 'Schedule')  // one place for it: status, the form, Run now, the runs
    expect(await within(sheet).findByLabelText(/^From/)).toHaveValue('06:00')
    expect(within(sheet).getByText('Runs every 5 minutes, Mon–Fri, 06:00–22:00 Toronto')).toBeInTheDocument()
    fireEvent.click(within(sheet).getByRole('radio', { name: '15 min' }))
    expect(within(sheet).getByText('Runs every 15 minutes, Mon–Fri, 06:00–22:00 Toronto')).toBeInTheDocument()  // live
    fireEvent.change(within(sheet).getByLabelText(/^To/), { target: { value: '' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save schedule' }))
    expect(await within(sheet).findByText('Give both the start and the end, or neither')).toBeInTheDocument()
    fireEvent.change(within(sheet).getByLabelText(/^To/), { target: { value: '23:30' } })
    fireEvent.click(within(within(sheet).getByRole('group', { name: 'Days' })).getByRole('button', { name: 'Sat' }))
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save schedule' }))
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

  for (const [name, setting, word, callout] of [
    ['Off here', { paused: true, system_minutes: 5, enabled: true }, 'Off', /Automatic runs are off:/],
    ['not available on this server', { paused: true, system_minutes: 5, enabled: false }, 'Not available', /not available on this server/],
  ] as const) {
    it(`${name}: the card, the Callout, and Next run says Off (in the list and the Schedule tab)`, async () => {
      api.scheduler.mockResolvedValue(setting)
      api.list.mockResolvedValue({ items: [{ ...ran, overdue: false }], total: 1 })
      renderAs(['sales.read'])
      expect(await card('Automatic runs', word)).toBeInTheDocument()
      expect(await screen.findByText(callout)).toBeInTheDocument()
      const row = (await screen.findByText('sftp-test')).closest('tr')!
      await waitFor(() => expect(within(cellOf(row, 'Next run')).getByText('Off')).toBeInTheDocument())
      expect(within(cellOf(row, 'Next run')).queryByText(/2026|Nov/)).toBeNull()  // no next-run time that never comes
      fireEvent.click(within(row).getByText('sftp-test'))
      const sheet = await screen.findByRole('dialog')
      await openTab(sheet, 'Schedule')
      expect(within(sheet).getByText(callout)).toBeInTheDocument()
      expect(within(within(sheet).getByRole('region', { name: 'Schedule' })).getByText('Off')).toBeInTheDocument()
    })
  }

  it('On: no Callout; an overdue connection is marked Overdue beside its next run', async () => {
    api.list.mockResolvedValue({ items: [{ ...ran, overdue: true }], total: 1 })
    renderAs(['sales.read'])
    await card('Automatic runs', 'On')
    const row = (await screen.findByText('sftp-test')).closest('tr')!
    await waitFor(() => expect(within(row).getByText('Overdue')).toBeInTheDocument())
    expect(screen.queryByText(/Automatic runs are/)).toBeNull()
    expect(within(cellOf(row, 'Next run')).queryByText('Off')).toBeNull()
  })
})

describe('the Automatic runs switch', () => {
  const off = { paused: true, system_minutes: 5, enabled: true }
  const sheetOf = async (word: string) => {
    fireEvent.click(await card('Automatic runs', word))
    return screen.findByRole('dialog', { name: 'Automatic runs' })
  }
  beforeEach(() => { api.saveScheduler.mockReset(); api.saveScheduler.mockResolvedValue({}) })

  it('not available on this server: no switch, one plain sentence', async () => {
    api.scheduler.mockResolvedValue({ ...off, enabled: false })
    renderAs(['sales.read', 'sales.config'])
    const sheet = await sheetOf('Not available')
    expect(await within(sheet).findByText(/An administrator must allow automatic runs on this server/)).toBeInTheDocument()
    expect(within(sheet).queryByRole('button', { name: /Turn automatic runs/ })).toBeNull()
    expect(within(sheet).queryByLabelText(/System job/)).toBeNull()
  })

  it('a reader sees it and cannot switch it', async () => {
    api.scheduler.mockResolvedValue(off)
    renderAs(['sales.read'])
    const sheet = await sheetOf('Off')
    expect(await within(sheet).findByText('Automatic runs: Off')).toBeInTheDocument()
    expect(within(sheet).queryByRole('button', { name: /Turn automatic runs/ })).toBeNull()
  })

  it('On asks first: which connections and how often; nothing is sent until confirmed', async () => {
    api.scheduler.mockResolvedValue(off)
    api.list.mockResolvedValue({ items: [conn, { ...conn, id: 8, name: 'held', schedule_paused: true }], total: 2 })
    renderAs(['sales.read', 'sales.config'])
    const sheet = await sheetOf('Off')
    const go = await within(sheet).findByRole('button', { name: 'Turn automatic runs on' })
    await waitFor(() => expect(go).toBeEnabled())
    fireEvent.click(go)
    const confirm = await screen.findByRole('dialog', { name: 'Turn automatic runs on?' })
    expect(confirm).toHaveTextContent('These connections start reading now: radiant (every 5 min)')
    expect(confirm).toHaveTextContent('they stay paused: held')
    expect(confirm).toHaveTextContent('The system job runs every 5 min')
    expect(confirm).toHaveTextContent('Sales acts on what it reads')
    expect(api.saveScheduler).not.toHaveBeenCalled()
    fireEvent.click(within(confirm).getByRole('button', { name: 'Turn on' }))
    await waitFor(() => expect(api.saveScheduler).toHaveBeenCalledWith({ paused: false }))  // the switch alone
  })

  it('Off needs no confirmation', async () => {
    renderAs(['sales.read', 'sales.config'])
    const sheet = await sheetOf('On')
    fireEvent.click(await within(sheet).findByRole('button', { name: 'Turn automatic runs off' }))
    await waitFor(() => expect(api.saveScheduler).toHaveBeenCalledWith({ paused: true }))
    expect(screen.queryByRole('dialog', { name: 'Turn automatic runs off?' })).toBeNull()
  })

  it('saving the system job\'s minutes sends only the minutes', async () => {
    api.scheduler.mockResolvedValue(off)
    renderAs(['sales.read', 'sales.config'])
    const sheet = await sheetOf('Off')
    fireEvent.change(await within(sheet).findByLabelText(/^System job/), { target: { value: '7' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.saveScheduler).toHaveBeenCalledWith({ system_minutes: 7 }))
  })
})

describe('the page: settings as cards, a plain table', () => {
  it('one row of cards; no panel pushes the table down', async () => {
    api.edi.mockResolvedValueOnce({ connection_id: 7, ack_timeout_minutes: 60 })
    renderAs(['sales.read'])
    const row = await screen.findByRole('region', { name: 'Connection settings' })
    await card('Warehouse', 'radiant')
    expect(row).toHaveTextContent(/997 wait\s*60 min/i)
    expect(row).toHaveTextContent(/810 invoices\s*None/i)
    expect(screen.queryByRole('region', { name: 'Shadow run' })).toBeNull()  // only in its sheet
  })

  it('the row: name, host under it, its type, one health badge; Kind, Mode and After read are not shown', async () => {
    renderAs(['sales.read'])
    const r = (await screen.findByText('sftp-test')).closest('tr')!
    expect(within(r).getByText('radiant')).toBeInTheDocument()
    expect(within(r).getByLabelText('SFTP')).toBeInTheDocument()
    expect(within(r).getByText('Key not pinned')).toBeInTheDocument()
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent)
    for (const gone of ['Kind', 'Host', 'Mode', 'After read']) expect(heads.join()).not.toContain(gone)
  })
})

describe('the Overview checklist', () => {
  const pinned = { ...conn, host_key: LINE }
  const openIt = async (c: ConnectionRead, keys = ['sales.read', 'sales.config', 'sales.file.retry']) => {
    api.list.mockResolvedValue({ items: [c], total: 1 })
    renderAs(keys)
    return open()
  }

  it('a missing secret says what to do, by its name only', async () => {
    const status = await openIt({ ...pinned, secret_present: false })
    expect(step(status, 'Secret on server')).toHaveAttribute('data-status', 'todo')
    expect(step(status, 'Secret on server')).toHaveTextContent('SALES_SECRET_SFTP_TEST')
  })

  it('a refused login offers Clear right there', async () => {
    api.clear.mockResolvedValue(pinned)
    const status = await openIt({ ...pinned, auth_failed_at: '2026-11-02T20:00:00Z', auth_failed_reason: 'bad password' })
    const login = step(status, 'Login OK')
    expect(login).toHaveAttribute('data-status', 'problem')
    expect(login).toHaveTextContent('bad password')
    fireEvent.click(within(login).getByRole('button', { name: 'Clear and try again next cycle' }))
    await waitFor(() => expect(api.clear).toHaveBeenCalledWith(7))
  })

  it('with the key pinned, Test sits on Folders and its result is a table', async () => {
    api.test.mockResolvedValue({ host_key: { line: LINE, fingerprint: 'SHA256:abc', pinned: true, matches: true }, error: null,
      folders: [{ folder: '/in', ok: true, files: 3, error: null }, { folder: '/out', ok: false, files: null, error: 'no such folder' }] })
    const status = await openIt(pinned)
    const folders = step(status, 'Folders reachable')
    expect(folders).toHaveAttribute('data-status', 'unknown')
    expect(within(step(status, 'Host key pinned')).queryByRole('button')).toBeNull()
    fireEvent.click(within(folders).getByRole('button', { name: 'Test connection' }))
    const table = await within(status).findByRole('table', { name: 'Test result' })
    expect(within(table).getByText('/in').closest('tr')).toHaveTextContent('3')
    expect(within(table).getByText('/out').closest('tr')).toHaveTextContent('no such folder')
    expect(step(status, 'Folders reachable')).toHaveAttribute('data-status', 'problem')
  })

  it('a key that differs from the pinned one is a problem, offered only as a replacement', async () => {
    api.test.mockResolvedValue({ host_key: { line: 'ssh-ed25519 OTHER', fingerprint: 'SHA256:new', pinned: true, matches: false }, folders: [], error: 'host key changed' })
    const status = await openIt(pinned)
    fireEvent.click(within(step(status, 'Folders reachable')).getByRole('button', { name: 'Test connection' }))
    await within(status).findByText('SHA256:new')
    expect(step(status, 'Host key pinned')).toHaveAttribute('data-status', 'problem')
    expect(within(step(status, 'Host key pinned')).getByRole('button', { name: 'Replace the pinned key with this one' })).toBeInTheDocument()
  })

  it('Run now is on the Overview and in the header, for sales.file.retry only', async () => {
    const status = await openIt(pinned)
    expect(within(step(status, 'Last run')).getByRole('button', { name: 'Run now' })).toBeInTheDocument()
    const sheet = screen.getByRole('dialog')
    expect(within(within(sheet).getByRole('toolbar', { name: 'Actions' })).getByRole('button', { name: 'Run now' })).toBeInTheDocument()
    cleanup()
    const read = await openIt(pinned, ['sales.read'])
    expect(within(step(read, 'Last run')).queryByRole('button')).toBeNull()
  })
})

describe('the Overview: where it reads and where it writes', () => {
  const bare = { ...conn, host_key: LINE, outbound_folder: '/to-partner', after_read: 'MOVE' as const }
  const step2 = async (c: ConnectionRead, edi: { connection_id: number | null; invoice_connection_id?: number | null }) => {
    api.edi.mockResolvedValue({ ack_timeout_minutes: 120, ...edi })
    api.list.mockResolvedValue({ items: [c], total: 1 })
    renderAs(['sales.read'])
    return open()
  }

  it('with Write off, the warehouse connection says what it sends is kept and not sent, and files are left', async () => {
    const status = await step2(bare, { connection_id: 7 })
    const writes = await within(status).findByRole('listitem', { name: 'Writes' })
    expect(writes).toHaveAttribute('data-status', 'todo')
    expect(writes).toHaveTextContent('warehouse connection')
    expect(writes).toHaveTextContent('kept and not sent')
    const reads = within(status).getByRole('listitem', { name: 'Reads' })
    expect(reads).toHaveTextContent('left in place (moving and deleting need Write)')
  })

  it('so is the 810 connection', async () => {
    const status = await step2(bare, { connection_id: null, invoice_connection_id: 7 })
    expect(await within(status).findByRole('listitem', { name: 'Writes' })).toHaveTextContent('810 connection')
  })

  it('with Write on, it names the write folder and where read files go', async () => {
    const status = await step2({ ...bare, can_write: true }, { connection_id: 7 })
    const writes = await within(status).findByRole('listitem', { name: 'Writes' })
    expect(writes).toHaveAttribute('data-status', 'done')
    expect(writes).toHaveTextContent('/to-partner')
    expect(within(status).getByRole('listitem', { name: 'Reads' })).toHaveTextContent('moved to /processed')
  })

  it('with Read off, nothing is read', async () => {
    const status = await step2({ ...bare, can_read: false }, { connection_id: 99 })
    expect(await within(status).findByRole('listitem', { name: 'Reads' })).toHaveTextContent('Read is off')
  })
})

describe('Read and Write: in the list and turned on or off from the Overview', () => {
  const sftpConn = { ...conn, host_key: LINE, outbound_folder: '/to-partner' }

  it('the list says whether each connection reads and writes, and where', async () => {
    api.list.mockResolvedValue({ items: [{ ...sftpConn, can_write: true }], total: 1 })
    renderAs(['sales.read'])
    const row = (await screen.findByText('sftp-test')).closest('tr')!
    expect(row).toHaveTextContent('/in')
    expect(row).toHaveTextContent('/to-partner')
    expect(within(row).getAllByText('On')).toHaveLength(2)
  })

  it('Turn on asks what it will do, then saves only the tick', async () => {
    api.list.mockResolvedValue({ items: [sftpConn], total: 1 })
    api.update.mockReset()
    api.update.mockResolvedValue({ ...sftpConn, can_write: true })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderAs(['sales.read', 'sales.config'])
    const status = await open()
    fireEvent.click(within(step(status, 'Writes')).getByRole('button', { name: 'Turn on' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(sftpConn.id, { can_write: true }, sftpConn.version))
    expect(confirm.mock.calls[0][0]).toContain('/to-partner')
    confirm.mockRestore()
  })

  it('a No changes nothing; without sales.config there is no button', async () => {
    api.list.mockResolvedValue({ items: [sftpConn], total: 1 })
    api.update.mockReset()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderAs(['sales.read', 'sales.config'])
    const status = await open()
    fireEvent.click(within(step(status, 'Reads')).getByRole('button', { name: 'Turn off' }))
    await waitFor(() => expect(confirm).toHaveBeenCalled())
    expect(api.update).not.toHaveBeenCalled()
    confirm.mockRestore()
    cleanup()
    renderAs(['sales.read'])
    const readOnly = await open()
    expect(within(step(readOnly, 'Writes')).queryByRole('button', { name: /Turn/ })).toBeNull()
  })

  it('the go-live gate refusal is said in plain words', async () => {
    api.list.mockResolvedValue({ items: [sftpConn], total: 1 })
    api.update.mockReset()
    api.update.mockRejectedValue(Object.assign(new Error('x'), { body: { error: { code: 'connection.go_live_gate', message: 'x' } } }))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderAs(['sales.read', 'sales.config'])
    const status = await open()
    fireEvent.click(within(step(status, 'Writes')).getByRole('button', { name: 'Turn on' }))
    expect(await screen.findByText(/Configuration → Notifications/)).toBeInTheDocument()
    confirm.mockRestore()
  })
})

describe('the form, by type of connection', () => {
  const headings = (el: HTMLElement) => within(el).getAllByRole('heading').map((h) => h.textContent)
  const newForm = async () => {
    renderAs(['sales.read', 'sales.config'])
    fireEvent.click(await screen.findByRole('button', { name: 'New' }))
    return screen.findByRole('dialog', { name: 'New connection' })
  }
  const form = (sheet: HTMLElement) => sheet.querySelector('form')!

  it('SFTP: its sections, no Shopify ones; every folder in view, by what it is for', async () => {
    const sheet = await newForm()
    expect(headings(form(sheet))).toEqual(['Basics', 'Reading', 'Writing', 'Login', 'Schedule'])  // Login is set once: after what it does
    for (const f of ['Read folder', 'Archive folder', 'Error folder', 'Write folder']) {
      expect(within(sheet).getByLabelText(new RegExp(`^${f}`)).closest('details')).toBeNull()
    }
    expect(within(sheet).queryByLabelText('API version')).toBeNull()
  })

  it('Shopify: no folders, no user name, Read and Write only; a saved one sends what the server needs', async () => {
    api.create.mockReset()
    api.create.mockResolvedValue({ ...conn, id: 9, kind: 'SHOPIFY' })
    api.get.mockResolvedValue({ ...conn, id: 9, kind: 'SHOPIFY' })
    const sheet = await newForm()
    fireEvent.click(within(sheet).getByRole('radio', { name: 'Shopify' }))
    expect(headings(form(sheet))).toEqual(['Basics', 'Reading', 'Writing', 'Shopify', 'Login', 'Schedule'])
    expect(within(sheet).queryByLabelText('User name')).toBeNull()
    expect(within(sheet).queryByLabelText(/^Read folder|^Write folder/)).toBeNull()
    fireEvent.change(within(sheet).getByLabelText(/^Name/), { target: { value: 'store' } })
    fireEvent.change(within(sheet).getByLabelText(/^Address/), { target: { value: 'x.myshopify.com' } })
    fireEvent.change(within(sheet).getByLabelText(/^API version/), { target: { value: '2026-04' } })
    fireEvent.change(within(sheet).getByLabelText(/^Partner/), { target: { value: 'SHOP' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledOnce())
    const sent = api.create.mock.calls[0][0]
    expect(sent).toMatchObject({ kind: 'SHOPIFY', after_read: 'LEAVE', stable_seconds: 60, port: 22, can_read: true, can_write: false })
    for (const k of ['username', 'inbound_folder', 'host_key']) expect(sent).not.toHaveProperty(k)
  })

  it('after New, the connection opens on its Overview', async () => {
    api.create.mockReset()
    api.create.mockResolvedValue({ ...conn, id: 9, name: 'new-one' })
    api.get.mockResolvedValue({ ...conn, id: 9, name: 'new-one' })
    const sheet = await newForm()
    for (const [label, value] of [[/^Name/, 'new-one'], [/^Address/, 'h'], [/^User name/, 'u'], [/^Password name/, 'SALES_SECRET_X'], [/^Read folder/, '/in']] as const) {
      fireEvent.change(within(sheet).getByLabelText(label), { target: { value } })
    }
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    const opened = await screen.findByRole('dialog', { name: 'new-one' })
    expect(await within(opened).findByRole('list', { name: 'Set-up checklist' })).toBeInTheDocument()
  })

  it('no technical name in what the user reads', () => {
    const hint = (f: (typeof connectionFields)[number], kind: string) => (typeof f.hint === 'function' ? f.hint({ kind }) : f.hint)
    const text = JSON.stringify(connectionFields.map((f) => [f.label, hint(f, 'SFTP'), hint(f, 'SHOPIFY')]))
    expect(text).not.toMatch(/host_key|secret_name|SALES_[A-Z]|FR-OB/)
  })

  it('Read and Write say what they do for each type; a new connection reads and does not write', async () => {
    const sheet = await newForm()
    expect(within(sheet).getByLabelText(/^Read$/)).toBeChecked()
    expect(within(sheet).getByLabelText(/^Write$/)).not.toBeChecked()
    expect(sheet).toHaveTextContent('Sales lists and reads the read folder')
    expect(sheet).toHaveTextContent('Sales puts its files in the write folder')
    fireEvent.click(within(sheet).getByRole('radio', { name: 'Shopify' }))
    expect(sheet).toHaveTextContent("Sales pulls the store's orders")
    expect(sheet).toHaveTextContent('Sales sends fulfilments and tracking to the store')
  })

  it('reading has its folder, archive and error folder; writing has one folder; there is no mode and no test folder', async () => {
    const sheet = await newForm()
    for (const label of ['Read folder', 'Archive folder', 'Error folder', 'Write folder']) {
      expect(within(sheet).getByLabelText(new RegExp(`^${label}`))).toBeInTheDocument()
    }
    expect(within(sheet).queryByLabelText(/Test outbound folder|Test inbound folder/)).toBeNull()
    expect(within(sheet).queryByRole('radio', { name: 'Live' })).toBeNull()
  })

  it('a server refusal naming a field is said in the form\'s words', async () => {
    api.create.mockReset()
    api.create.mockRejectedValue(Object.assign(new Error('x'), { body: { error: { code: 'validation', message: 'x',
      details: { errors: [{ field: 'secret_name', message: 'secret_name must be a name' }] } } } }))
    const sheet = await newForm()
    for (const [label, value] of [[/^Name/, 'n'], [/^Address/, 'h'], [/^User name/, 'u'], [/^Password name/, 'SALES_SECRET_X'], [/^Read folder/, '/in']] as const) {
      fireEvent.change(within(sheet).getByLabelText(label), { target: { value } })
    }
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    expect(await within(sheet).findByText('Password name on the server must be a name')).toBeInTheDocument()
  })
})

