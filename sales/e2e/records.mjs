// Records in their own tabs (stage 1 of the records work, user 2026-11-27), in a real browser: a list row's sheet has "Open in
// its tab" as its first button and moves the record to its own tab (/orders/:id); an item code in the order's lines is a link
// to the item's own tab, whose "← Back to Order …" closes it and returns to the order's tab; a second click on the same link
// focuses that tab (one per record); while a record's tab is open, its sheet beside a list is read-only and says so; right
// click on a link gives Open / Open in a new browser tab / Copy link; a lot's link opens the lot's page; Copy link from the
// record's ⋯ gives the record's address, and that address opened cold shows the record with "← Back to <its list>";
// on a phone the header has no buttons, only ⋯. Fails (exit 1) with what failed.
//
// Fully mocked like searchPanel.mjs (sales/e2e/fixtures; no token, no database). Run from the repository root against a running
// Vite of sales/ (BASE), like searchPanel.mjs, with `node e2e/records.mjs`.
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import * as F from './fixtures/data.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(process.env.PW ? path.join(process.env.PW, 'x.js') : import.meta.url)
const { chromium } = require('playwright-core')
const BASE = (process.env.BASE ?? 'http://localhost:5182').replace(/\/$/, '')
const SHOTS = process.env.SHOTS
const META = JSON.parse(fs.readFileSync(path.join(here, 'fixtures/meta.json'), 'utf8'))
const EXEC = process.env.CHROMIUM ?? fs.readdirSync('/ms-playwright').filter((d) => d.startsWith('chromium_headless_shell'))
  .map((d) => `/ms-playwright/${d}/chrome-linux/headless_shell`).find((p) => fs.existsSync(p))
const PERMS = ['sales.read', 'sales.order.operate', 'sales.invoice.manage', 'sales.config', 'sales.settle']
const session = { state: { token: 'placeholder-not-a-token', refreshToken: null, isAuthenticated: true,
  user: { id: '00000000-0000-0000-0000-000000000001', email: 'e2e@example.invalid', full_name: 'E2E', role: 'system_admin', department_id: null } }, version: 0 }
const ITEM = (code, i) => ({ ...F.items[0], id: i, code, item_code: code, description: `Sample item ${i}` })
const LOT_ROWS = (lot) => [1, 2].map((i) => ({ id: i, lot, item_code: 'ITEM-1', expiry: '2028-01-31', days_to_expiry: 400, qty_shipped_ea: 60, sscc: null,
  tracking: null, carrier: null, tracking_url: null, shipment_id: 1, document_id: 9, ship_date: '2026-11-06', bol: null, our_reference: null,
  applied: true, shadow: false, order_id: 30, order_ref: 'SMP-1030', order_number: 30, partner_code: 'SAMPLE', partner_name: 'Sample partner',
  ship_to_city: 'Toronto', ship_to_postal: 'M5V' }))

function filterValue(u, field) {
  const f = u.searchParams.getAll('filter').find((x) => x.startsWith(`${field}:`))
  return f ? decodeURIComponent(f.split(':').slice(2).join(':')) : null
}

const asked = []  // every API address the page read (the scope must ride in them)

function answer(url) {
  const u = new URL(url)
  asked.push(u.pathname + u.search)
  const p = u.pathname.replace(/^\/api\/v1\//, '')
  if (p === 'config/me/permissions') return { permissions: Object.fromEntries(PERMS.map((k) => [k, true])), roles: [] }
  if (p === 'config/public/branding') return { name: 'UniOps' }
  if (p === 'views' || p.startsWith('config/notification')) return []
  if (p === 'help/texts') return META['help/texts']
  if (p.endsWith('/meta')) return META[p.slice(0, -5)] ?? { version: 1, fields: [], views: [] }
  if (p.endsWith('/count')) return { total: 30 }
  if (p === 'orders') return { items: F.orders, total: F.orders.length }
  const order = p.match(/^orders\/(\d+)$/)
  if (order) return F.orderDetail(Number(order[1]))
  if (p === 'config/items') {
    const code = filterValue(u, 'item_code')
    const all = ['ITEM-1', 'ITEM-2', 'ITEM-3'].map(ITEM)
    const rows = code ? all.filter((r) => r.item_code === code) : all
    return { items: rows, total: rows.length }
  }
  if (p === 'inventory/items') {
    const code = filterValue(u, 'item_code')
    const rows = F.stockItems.filter((r) => !code || r.item_code === code)
    return { items: rows, total: rows.length }
  }
  if (p === 'lots') {
    const lot = filterValue(u, 'lot')
    const rows = LOT_ROWS(lot ?? 'LOT-1')
    return { items: rows, total: rows.length, groups: u.searchParams.get('group') ? [{ key: ['SMP-1030'], count: 2 }] : undefined,
      totals: [{ currency: null, count: 2, sums: { qty_shipped_ea: '120' } }] }
  }
  return { items: [], total: 0 }
}

const failures = []
const check = (ok, what) => { if (!ok) failures.push(what) }

async function run(name, viewport) {
  const phone = name === 'phone'
  const browser = await chromium.launch({ executablePath: EXEC, headless: true })
  const ctx = await browser.newContext({ viewport, isMobile: phone, hasTouch: phone, permissions: ['clipboard-read', 'clipboard-write'] })
  await ctx.addInitScript((s) => localStorage.setItem('sales-auth', s), JSON.stringify(session))
  await ctx.route('**/*', (route) => {
    const req = route.request()
    if (!new URL(req.url()).pathname.startsWith('/api/')) return route.continue()
    if (req.method() !== 'GET') return route.fulfill({ status: 409, contentType: 'application/json', body: '{"error":{"code":"e2e","message":"read only","details":{}}}' })
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(req.url())) })
  })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => failures.push(`${name}: page error ${String(e).slice(0, 200)}`))
  const shot = async (what) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: `${SHOTS}/${what}-${name}.png` }) } }
  const step = async (what, fn) => { try { await fn() } catch (e) { failures.push(`${name} ${what}: ${String(e.message ?? e).split('\n')[0]}`); await shot(`failed-${failures.length}`) } }
  const tabBar = page.locator('[data-shell-tabbar]')
  const tabTitles = async () => (await tabBar.locator('[role="tab"], button, a').allInnerTexts()).map((t) => t.trim()).filter(Boolean)
  const shown = () => page.locator('main div.absolute.inset-0.overflow-y-auto:not([hidden])')

  await page.goto(`${BASE}/orders`, { waitUntil: 'networkidle', timeout: 60000 })
  await page.locator(phone ? '[data-row-card]' : 'table tbody tr').first().waitFor({ timeout: 15000 })

  if (phone) {
    await step('a phone: the header has no buttons, only ⋯', async () => {
      await page.locator('[data-row-card]').first().click()
      const sheet = page.getByRole('dialog').first()
      const bar = sheet.getByRole('toolbar', { name: 'Record actions' })
      await bar.waitFor({ timeout: 10000 })
      const labels = (await bar.getByRole('button').allInnerTexts()).map((t) => t.trim())
      check(labels.every((l) => l === ''), `phone: the header shows only ⋯ (${JSON.stringify(labels)})`)
      await shot('sheet-header')
    })
    await browser.close()
    return
  }

  await step('a row\'s sheet: Open in its tab is its first button and moves it to a tab of its own', async () => {
    await page.getByText('SMP-1030').first().click()
    const sheet = page.getByRole('dialog', { name: 'SMP-1030' })
    const bar = sheet.getByRole('toolbar', { name: 'Record actions' })
    await bar.waitFor({ timeout: 10000 })
    const first = (await bar.getByRole('button').first().innerText()).trim()
    check(first === 'Open in its tab', `the sheet's first button is Open in its tab (${first})`)
    await shot('sheet')
    await bar.getByRole('button', { name: 'Open in its tab' }).click()
    await page.waitForURL(/\/orders\/30$/, { timeout: 5000 })
    await shown().getByRole('link', { name: '← Back to Orders' }).waitFor({ timeout: 10000 })
    check((await page.getByRole('dialog', { name: 'SMP-1030' }).count()) === 0, 'the sheet closed')
    check((await tabTitles()).some((t) => t.includes('SMP-1030')), `a tab named after the order (${(await tabTitles()).join(' | ')})`)
  })

  await step('an item in the order\'s lines opens the item\'s tab; Back returns to the order', async () => {
    await shown().getByRole('tab', { name: /^Lines/ }).click()
    const link = shown().getByRole('link', { name: 'ITEM-1', exact: true })
    await link.waitFor({ timeout: 5000 })
    check((await link.getAttribute('href')) === '/items/ITEM-1', 'the item link is a real address')
    await link.click()
    await page.waitForURL(/\/items\/ITEM-1$/, { timeout: 5000 })
    const back = shown().getByRole('link', { name: '← Back to SMP-1030' })
    await back.waitFor({ timeout: 10000 })
    await shot('item-tab')
    await back.click()
    await page.waitForURL(/\/orders\/30$/, { timeout: 5000 })
    check(!(await tabTitles()).includes('ITEM-1'), `Back closed the item's tab (${(await tabTitles()).join(' | ')})`)
  })

  await step('right click on a link: Open, Open in a new browser tab, Copy link', async () => {
    await page.goto(`${BASE}/orders/30`, { waitUntil: 'networkidle' })
    await shown().getByRole('tab', { name: /^Lines/ }).click()
    const link = shown().getByRole('link', { name: 'ITEM-2', exact: true })
    await link.click({ button: 'right' })
    const menu = page.getByRole('menu')
    await menu.waitFor({ timeout: 5000 })
    const items = (await menu.getByRole('menuitem').allInnerTexts()).map((t) => t.split('\n')[0].trim())
    check(JSON.stringify(items) === JSON.stringify(['Open', 'Open in a new browser tab', 'Copy link']), `the link menu (${JSON.stringify(items)})`)
    await shot('link-menu')
    await menu.getByRole('menuitem', { name: /^Copy link/ }).click()
    const copied = await page.evaluate(() => navigator.clipboard.readText()).catch(() => '')
    check(copied.endsWith('/items/ITEM-2'), `Copy link gives the item's address (${copied})`)
  })

  await step('a lot in the Lots list opens the lot\'s page; a cold address goes back to its list', async () => {
    await page.goto(`${BASE}/lots`, { waitUntil: 'networkidle' })
    const lot = shown().getByRole('link', { name: 'LOT-1', exact: true }).first()
    await lot.click()
    await page.waitForURL(/\/lots\/LOT-1$/, { timeout: 5000 })
    await shown().getByRole('region', { name: 'LOT-1' }).waitFor({ timeout: 10000 })
    await shot('lot-tab')
    await page.goto(`${BASE}/items/ITEM-3`, { waitUntil: 'networkidle' })
    await shown().getByRole('link', { name: '← Back to Items' }).waitFor({ timeout: 10000 })
  })

  await step('the item\'s sheet in the Items list is read-only while its tab is open', async () => {
    await page.goto(`${BASE}/items/ITEM-1`, { waitUntil: 'networkidle' })
    await shown().getByRole('region', { name: 'ITEM-1' }).waitFor({ timeout: 10000 })
    await page.goto(`${BASE}/config/items`, { waitUntil: 'networkidle' })
    // A reload empties the tabs (as designed): open the item's tab again from a link, then the list's row.
    await shown().getByText('ITEM-1', { exact: true }).first().click()
    const sheet = page.getByRole('dialog', { name: 'ITEM-1' })
    await sheet.waitFor({ timeout: 10000 })
    await sheet.getByRole('toolbar', { name: 'Record actions' }).getByRole('button', { name: 'Open in its tab' }).click()
    await page.waitForURL(/\/items\/ITEM-1$/, { timeout: 5000 })
    await tabBar.getByText('Items', { exact: true }).first().click()
    await shown().getByText('ITEM-1', { exact: true }).first().click()
    const again = page.getByRole('dialog', { name: 'ITEM-1' })
    await again.getByRole('region', { name: 'Open in its tab' }).waitFor({ timeout: 10000 })
    await shot('read-only-sheet')
    check((await again.getByRole('button', { name: 'Edit item' }).count()) === 0, 'the read-only sheet offers no Edit item')
    check((await again.getByRole('button', { name: 'Save' }).count()) === 0, 'the read-only sheet offers no Save')
  })
  await step('the item\'s own page: facts first, the form behind Edit item, its Orders scoped by the server', async () => {
    await page.goto(`${BASE}/items/ITEM-1`, { waitUntil: 'networkidle' })
    const item = shown().getByRole('region', { name: 'ITEM-1' })
    await item.waitFor({ timeout: 10000 })
    check((await item.locator('#sf-description').count()) === 0, 'no form until Edit item')
    await item.getByText('480', { exact: true }).first().waitFor({ timeout: 5000 })  // available EA, a fact
    const tabs = (await item.getByRole('tab').allInnerTexts()).map((t) => t.split('\n')[0].trim())
    check(JSON.stringify(tabs) === JSON.stringify(['Overview', 'Lots', 'Stock card', 'Shipped', 'Orders', 'Invoices', 'Transfers', 'Partner codes', 'History']),
      `the item's tabs (${JSON.stringify(tabs)})`)
    await shot('item-overview')
    await item.getByRole('button', { name: 'Edit item' }).click()
    await item.locator('#sf-description').waitFor({ timeout: 5000 })
    await item.getByRole('button', { name: 'Cancel' }).click()
    await item.getByRole('tab', { name: /^Orders/ }).click()
    await item.getByRole('note', { name: 'Scope: Item = ITEM-1' }).waitFor({ timeout: 10000 })
    await shot('item-orders')
    const scoped = asked.filter((a) => a.startsWith('/api/v1/orders?') && a.includes('scope=item_code%3Aeq%3AITEM-1'))
    check(scoped.length > 0, 'the Orders tab reads with the scope')
    check(asked.some((a) => a.startsWith('/api/v1/shipped/count?') && a.includes('scope=')), 'the tab counts are counted with the scope')
  })
  await browser.close()
}

await run('desktop', { width: 1440, height: 900 })
await run('phone', { width: 390, height: 844 })
if (failures.length) {
  console.log('FAILED:\n' + failures.map((f) => `  - ${f}`).join('\n'))
  process.exit(1)
}
console.log('records: all checks passed')
