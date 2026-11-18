// Responsive check (the user, 2026-11-13 / 2026-11-16): the Sales front end at a phone width (390 px)
// and a desktop width (1440 px). Fails (exit 1, with what failed) when a page or sheet scrolls
// sideways, or when a phone list / form lacks its pinned status row or ActionBar.
//
// Fully mocked: every /api/ request is answered from sales/e2e/fixtures (synthetic data; list meta
// built from the code by fixtures/gen_meta.py). No token, no database, no dev2 data. The login is a
// placeholder in localStorage that no server ever sees.
//
// Run from the repository root, against a running Vite dev server or `vite preview` of sales/
// (BASE = its URL; e.g. dev2's Vite serves this working tree at http://10.10.50.64:5382):
//
//   docker run --rm --network host --user "$(id -u)" -e HOME=/tmp -e BASE=http://10.10.50.64:5382 \
//     -v "$PWD/sales":/w -w /w mcr.microsoft.com/playwright:v1.50.0-noble \
//     sh -c 'npm i --prefix /tmp/pw --no-save --no-audit --no-fund playwright-core@1.50.0 >/dev/null && PW=/tmp/pw/node_modules npm run -s e2e:responsive'
//
// About 40 s (most of it the npm install of playwright-core). Not in vitest or the default gates:
// it needs a running front end and a browser. Screenshots: SHOTS=<dir> (in the container, e.g. /tmp/shots).
// PLANT=overflow: a planted wide element, to see it fail (exit 1).
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

const PERMS = ['sales.read', 'sales.order.operate', 'sales.invoice.manage', 'sales.inventory.adjust', 'sales.transfer.manage', 'sales.config']
const session = { state: { token: 'placeholder-not-a-token', refreshToken: null, isAuthenticated: true,
  user: { id: '00000000-0000-0000-0000-000000000001', email: 'e2e@example.invalid', full_name: 'E2E', role: 'system_admin', department_id: null } }, version: 0 }

function answer(url) {
  const u = new URL(url)
  const p = u.pathname.replace(/^\/api\/v1\//, '')
  if (p === 'config/me/permissions') return { permissions: Object.fromEntries(PERMS.map((k) => [k, true])), roles: [] }
  if (p === 'config/public/branding') return { name: 'UniOps' }
  if (p === 'views' || p === 'config/notification-events' || p === 'config/notification-users') return []
  if (p === 'help/texts') return META['help/texts']
  if (p.endsWith('/meta')) return META[p.slice(0, -5)] ?? { version: 1, fields: [], views: [] }
  if (p === 'orders') return { items: F.orders, total: F.orders.length }
  const order = p.match(/^orders\/(\d+)$/)
  if (order) return F.orderDetail(Number(order[1]))
  if (p === 'payments') {
    const grouped = u.searchParams.getAll('group').length > 0
    return { items: F.payments, total: F.payments.length, totals: F.totalsOf(F.payments),
      ...(grouped ? { groups: F.paymentGroups, groups_truncated: false } : {}) }
  }
  if (p === 'inventory/items') return { items: F.stockItems, total: F.stockItems.length }
  if (p === 'inventory/lots') {
    const code = (u.searchParams.getAll('filter').join().match(/item_code:eq:([^,]+)/) ?? [])[1] ?? 'ITEM-1'
    return { items: F.stockLots(code), total: 2 }
  }
  if (p === 'operations/health') return F.health
  if (p === 'operations/dashboard') return F.dashboard
  if (p === 'config/partners') return { items: F.partners, total: F.partners.length }
  if (p === 'config/items') return { items: F.items, total: F.items.length }
  return { items: [], total: 0 }
}

// Every route in src/app/routes.tsx.
const ROUTES = ['/home', '/orders', '/transfers', '/deliveries', '/invoices', '/payments', '/documents', '/runs', '/inventory', '/lots',
  '/health', '/notifications', '/config/partners', '/config/partner-codes', '/config/items', '/config/dc-routing', '/config/warehouses',
  '/config/carriers', '/config/connections', '/config/notifications']

const failures = []
const notes = []
const check = (ok, what) => { (ok ? notes : failures).push(what) }

async function measure(page) {
  return page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]')
    return {
      page: document.documentElement.scrollWidth - window.innerWidth,
      sheet: dialog ? dialog.scrollWidth - dialog.clientWidth : 0,
      cards: document.querySelectorAll('[data-row-card]').length,
      statusBar: !!document.querySelector('[data-status-bar]'),
    }
  })
}

async function run(name, viewport) {
  const phone = name === 'phone'
  const browser = await chromium.launch({ executablePath: EXEC, headless: true })
  const ctx = await browser.newContext({ viewport, isMobile: phone, hasTouch: phone })
  await ctx.addInitScript((s) => localStorage.setItem('sales-auth', s), JSON.stringify(session))
  // PLANT=overflow proves the check catches what it is for: a 2000 px wide element on every page.
  if (process.env.PLANT === 'overflow') {
    await ctx.addInitScript(() => document.addEventListener('DOMContentLoaded', () => {
      const d = document.createElement('div'); d.style.width = '2000px'; d.style.height = '1px'; document.body.appendChild(d)
    }))
  }
  await ctx.route('**/*', (route) => {
    const req = route.request()
    if (!new URL(req.url()).pathname.startsWith('/api/')) return route.continue()
    if (req.method() !== 'GET') return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: { code: 'e2e', message: 'mock: read only', details: {} } }) })
    return route.fulfill({ status: 200, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' }, body: JSON.stringify(answer(req.url())) })
  })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => failures.push(`${name}: page error ${String(e).slice(0, 200)}`))
  const shot = async (what) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: `${SHOTS}/${what}-${name}.png` }) } }
  const step = async (what, fn) => {
    try { await fn() } catch (e) { failures.push(`${name} ${what}: ${String(e.message ?? e).split('\n')[0]}`) }
  }
  const listChecks = async (what) => {
    const m = await measure(page)
    check(m.page <= 0, `${name} ${what}: page sideways overflow ${m.page}px`)
    check(m.statusBar, `${name} ${what}: status row present`)
    check(phone ? m.cards > 0 : m.cards === 0, `${name} ${what}: ${phone ? 'cards on a phone' : 'a table on a desktop'} (cards=${m.cards})`)
    await shot(what)
  }
  const open = async (url) => { await page.goto(`${BASE}${url}`, { waitUntil: 'networkidle', timeout: 60000 }); await page.waitForTimeout(500) }

  await step('orders', async () => {
    await open('/orders')
    await listChecks('orders')
    await page.getByText('SMP-1030').first().click()
    await page.getByRole('dialog').waitFor({ timeout: 10000 })
    await page.waitForTimeout(400)
    const m = await measure(page)
    check(m.sheet <= 0, `${name} order sheet: sideways overflow ${m.sheet}px`)
    // Help: the status badge opens what the status means; the line under it says what to do.
    await page.getByRole('dialog').getByRole('button', { name: /^What ".+" means$/ }).first().click()
    const tip = page.locator('[data-help-content]')
    await tip.waitFor({ timeout: 5000 })
    const box = await tip.boundingBox()
    check(!!box && box.x >= 0 && box.x + box.width <= viewport.width, `${name} status help: opens inside the screen`)
    await shot('status-help')
    check(await page.locator('[role="dialog"] [data-help="record.next"]').count() === 1, `${name} order sheet: the next step is shown`)
    await page.keyboard.press('Escape')
    // the record's own tour: "Show me around", drawn inside the sheet; Esc ends it and the sheet stays
    const around = page.getByRole('dialog').getByRole('button', { name: 'Show me around' })
    await around.waitFor({ timeout: 5000 })
    await around.click()
    const step = page.locator('[data-tour-step]')
    await step.waitFor({ timeout: 5000 })
    check((await step.textContent()).includes('The record'), `${name} order: "Show me around" starts at the record's header`)
    await shot('record-tour')
    await page.keyboard.press('Escape')
    await page.waitForTimeout(200)
    check(await step.count() === 0 && await page.getByRole('dialog').count() >= 1, `${name} order: Esc ends the record tour, the sheet stays open`)
    await page.getByRole('tab', { name: /Lines/ }).click()
    await page.getByRole('dialog').getByRole('button', { name: /Show details/ }).first().click()
    await page.waitForTimeout(300)
    const l = await measure(page)
    check(l.sheet <= 0, `${name} order lines with packing: sideways overflow ${l.sheet}px`)
    await shot('order-lines')
    await page.keyboard.press('Escape')
  })
  await step('more fields', async () => {
    // Any field of the rows as a column (user 2026-11-18): the eye lists "More fields"; one ticked shows in the table / card.
    await open('/orders')
    await page.getByRole('button', { name: 'Show, hide or move columns' }).first().click()
    const more = page.getByRole('group', { name: 'More fields' })
    await more.waitFor({ timeout: 5000 })
    const box = await page.locator('[data-radix-popper-content-wrapper]').last().boundingBox()
    check(!!box && box.x >= 0 && box.x + box.width <= viewport.width + 1, `${name} column chooser: fits the screen`)
    check(await page.getByRole('searchbox', { name: 'Find a field' }).count() === 1, `${name} column chooser: has its find box`)
    await shot('more-fields')
    await page.getByRole('checkbox', { name: 'Subtotal' }).click()
    await page.keyboard.press('Escape')
    await page.waitForTimeout(300)
    await shot('more-fields-added')
    check(await page.getByText(/110\.00/).count() > 0, `${name} column chooser: the field ticked shows (Subtotal 110.00)`)
  })
  if (!phone) await step('drag to group', async () => {
    // Grouping by drag and drop (user 2026-11-18): a header's grip onto the Grouping row groups by it.
    // The browser's own drag events with one DataTransfer (a pointer-driven HTML5 drag does not run headless).
    await open('/payments')
    // The first column stays put while the table scrolls sideways (user 2026-11-18).
    const first = page.locator('thead th[data-frozen]:visible').first()
    const x0 = (await first.boundingBox()).x
    await page.locator('[data-table-scroll]:visible').first().evaluate((el) => { el.scrollLeft = 400 })
    await page.waitForTimeout(200)
    const scrolled = await page.locator('[data-table-scroll]:visible').first().evaluate((el) => el.scrollLeft)
    const x1 = (await first.boundingBox()).x
    check(scrolled > 0 && Math.abs(x1 - x0) < 1, `${name} frozen column: the first column stays while the table scrolls (scrolled ${scrolled}px, x ${x0} → ${x1})`)
    await shot('frozen-column')
    await page.locator('[data-table-scroll]:visible').first().evaluate((el) => { el.scrollLeft = 0 })
    // Locked columns (user 2026-11-18): "Lock column" joins the locked group at the left; no lock icon in the headers;
    // on a narrow window only those that fit freeze, and the column chooser says so.
    for (const col of ['Order ref', 'Customer']) {
      await page.getByRole('button', { name: `${col} column menu` }).first().click()
      await page.getByRole('menuitem', { name: /^Lock column/ }).click()
      await page.waitForTimeout(250)
    }
    check(await page.locator('thead th[data-frozen]:visible').count() === 3, `${name} lock columns: three columns locked from the menu`)
    check(await page.locator('thead .lucide-lock:visible').count() === 0, `${name} lock columns: no lock icon in the headers`)
    await page.setViewportSize({ width: 1024, height: 800 })
    await page.waitForTimeout(400)
    const frozenNarrow = await page.locator('thead th[data-frozen]:visible').count()
    await page.getByRole('button', { name: 'Show, hide or move columns' }).first().click()
    const note = await page.getByRole('note').filter({ hasText: 'locked columns frozen' }).first().textContent().catch(() => '')
    check(frozenNarrow < 3 && /of 3 locked columns frozen: the window is too narrow/.test(note ?? ''),
      `${name} lock columns: a narrow window freezes only what fits (${frozenNarrow} of 3) and the chooser says so`)
    await shot('locked-narrow')
    await page.keyboard.press('Escape')
    await page.setViewportSize(viewport)
    for (const col of ['Customer', 'Order ref']) {
      await page.getByRole('button', { name: `${col} column menu` }).first().click()
      await page.getByRole('menuitem', { name: /^Unlock column/ }).click()
      await page.waitForTimeout(250)
    }
    const tableTop = async () => (await page.locator('thead:visible').first().boundingBox()).y
    const top0 = await tableTop()
    const dt = await page.evaluateHandle(() => new DataTransfer())
    const grip = page.locator('[data-column-grip="method"]').first()
    await grip.dispatchEvent('dragstart', { dataTransfer: dt })
    const zone = page.locator('[data-group-drop]')
    await zone.waitFor({ timeout: 5000 })
    check(Math.abs((await tableTop()) - top0) < 1, `${name} drag to group: the table does not move when the Grouping row shows`)
    check(await zone.getAttribute('data-group-drop') === 'open', `${name} drag to group: the Grouping row opens for a groupable column`)
    await zone.dispatchEvent('dragenter', { dataTransfer: dt })
    await zone.dispatchEvent('dragover', { dataTransfer: dt })
    await shot('drag-to-group')
    await zone.dispatchEvent('drop', { dataTransfer: dt })
    await grip.dispatchEvent('dragend', { dataTransfer: dt })
    await page.waitForTimeout(600)
    check(await page.locator('[data-group-chip="method"]').count() === 1, `${name} drag to group: dropping Method on Grouping groups by it`)
    await shot('dragged-group')
  })
  if (phone) await step('upload form with the keyboard up', async () => {
    // A form with a file and a date-time (step 2, 2026-11-18): its ActionBar stays reachable while the soft keyboard
    // is up — simulated by focusing the date-time field and taking the keyboard's height off the window.
    await open('/inventory')
    await page.getByRole('button', { name: 'Radiant reports' }).first().click()
    await page.getByRole('button', { name: 'Upload report' }).first().click()
    const form = page.getByRole('dialog')
    await form.waitFor({ timeout: 5000 })
    check(await form.getByRole('button', { name: 'Choose a file' }).count() === 1, `${name} upload form: the file field`)
    await form.getByLabel('Taken at').first().focus()
    await page.setViewportSize({ width: viewport.width, height: 400 })
    await page.waitForTimeout(300)
    const upload = form.getByRole('button', { name: 'Upload' })
    const box = await upload.boundingBox()
    check(!!box && box.y >= 0 && box.y + box.height <= 400, `${name} upload form: Upload stays on screen with the keyboard up`)
    await shot('upload-keyboard')
    await page.setViewportSize(viewport)
    await page.keyboard.press('Escape')
  })
  await step('page help', async () => {
    await open('/orders')
    await page.getByRole('button', { name: 'Help: Orders' }).click()
    const panel = page.getByRole('dialog', { name: 'Help: Orders' })
    await panel.waitFor({ timeout: 5000 })
    const box = await panel.boundingBox()
    check(!!box && box.x >= 0 && box.x + box.width <= viewport.width + 1, `${name} page help: the panel fits the screen`)
    check(await panel.getByRole('group', { name: 'Tours' }).count() === 1, `${name} page help: the drawer has its Tours`)
    await shot('drawer')
    check(await panel.locator('[data-help-row="list.search"]').count() === 1, `${name} page help: the list tools are among its tips`)
    for (const tip of ['list.statusFind', 'list.chips', 'list.rowOpen', 'list.pages', 'list.notices']) {
      check(await panel.locator(`[data-help-row="${tip}"]`).count() === 1, `${name} page help: the table tip ${tip} is listed`)
    }
    await panel.getByRole('button', { name: /^All Sales tips/ }).click()
    check(await panel.getByRole('region', { name: /^All Sales tips/ }).count() === 1, `${name} page help: the catalog of every tip opens in the drawer`)
    await shot('catalog')
    await panel.getByRole('button', { name: 'Back' }).click()
    await panel.getByRole('searchbox', { name: 'Search help' }).fill('pallet')
    check(await panel.getByText('SSCC — term').count() === 1, `${name} page help: search finds a glossary term`)
    await shot('page-help')
    await page.keyboard.press('Escape')
    // The Orders tour: every stop is on screen and Next walks to Done.
    await page.getByRole('button', { name: 'Help: Orders' }).click()
    await page.getByRole('button', { name: /^Show me around this page/ }).click()
    let stops = 0
    for (let n = 0; n < 12; n++) {
      const box = page.locator('[data-tour-step]')
      await box.waitFor({ timeout: 5000 })
      stops++
      const r = await box.boundingBox()
      check(!!r && r.x >= 0 && r.x + r.width <= viewport.width + 1, `${name} orders tour: stop ${stops} fits the screen`)
      if (stops === 4) await shot('tour-stop')
      const ring = await page.locator('[data-tour-light]').boundingBox()
      check(!!ring && ring.x > 1 && ring.y > 1, `${name} orders tour: stop ${stops} lights its element, not the corner`)
      const done = box.getByRole('button', { name: 'Done' })
      if (await done.count()) { await done.click(); break }
      await box.getByRole('button', { name: 'Next' }).click()
    }
    check(stops >= 5 && stops <= 7, `${name} orders tour: short, from the page's own stops (${stops})`)
    check(await page.locator('[data-tour-step]').count() === 0, `${name} orders tour: Done ends it`)
    // Seen per stop: after the Orders tour, another list (the same list tools) has nothing new
    await open('/payments')
    await page.waitForTimeout(500)
    check(await page.locator('[data-help-new]').count() === 0, `${name} payments: no "new" hint after the Orders tour (same tools, seen)`)
    await open('/orders')
    await page.waitForTimeout(500)
    // "?" opens the page's help; Esc closes it
    await page.locator('body').press('?')
    check(await page.getByRole('dialog', { name: 'Help: Orders' }).count() === 1, `${name} "?" opens the page help`)
    await page.keyboard.press('Escape')
    // every list tool marks itself (a tour can point at it)
    for (const id of ['list.search', 'list.searchPanel', 'list.views', 'list.columns', 'list.refresh', ...(phone ? ['list.phone'] : [])]) {
      check(await page.locator(`[data-help="${id}"]`).count() >= 1, `${name} orders: ${id} is marked`)
    }
  })
  await step('payments', async () => {
    await open('/payments')
    await page.getByRole('button', { name: /^View:/ }).first().click()
    await page.getByText('PayPal — weekly').first().click()
    await page.waitForTimeout(700)
    const heads = await page.locator('[data-group-head], tr:has(button[aria-expanded]) td[colspan]').count()
    check(heads >= 2, `${name} payments weekly: group headers (${heads})`)
    await listChecks('payments-weekly')
  })
  await step('inventory', async () => {
    await open('/inventory')
    await page.getByRole('button', { name: /Show details/ }).first().click()
    await page.waitForTimeout(600)
    check(await page.locator('[data-row-detail]').count() > 0, `${name} inventory: a row opens to its lots`)
    check(await page.getByRole('button', { name: 'What "On hand" means' }).count() > 0, `${name} inventory: the On hand column says what it holds`)
    await listChecks('inventory-lots')
  })
  // Every page of the module (data or not): nothing scrolls the page sideways.
  for (const route of ROUTES) {
    await step(`page ${route}`, async () => {
      await open(route)
      const m = await measure(page)
      check(m.page <= 0, `${name} ${route}: page sideways overflow ${m.page}px`)
      const broken = await page.getByText(/failed to render/i).locator('visible=true').count()
      check(broken === 0, `${name} ${route}: renders (no error boundary)`)
      await shot(`page${route.replaceAll('/', '-')}`)
    })
  }
  if (phone) {
    await step('new order form', async () => {
      await open('/orders')
      await page.getByRole('button', { name: 'New manual order' }).click()
      await page.getByRole('dialog').waitFor({ timeout: 10000 })
      await page.waitForTimeout(500)
      const f = await page.evaluate(() => {
        const d = document.querySelector('[role="dialog"]')
        const bar = d?.querySelector('[data-action-bar]')
        const r = bar?.getBoundingClientRect()
        return { over: d ? d.scrollWidth - d.clientWidth : 0, bar: !!bar, sticky: bar ? getComputedStyle(bar).position === 'sticky' : false,
          inView: !!r && r.bottom <= window.innerHeight && r.top >= 0 }
      })
      check(f.over <= 0, `${name} new order form: sideways overflow ${f.over}px`)
      check(f.bar && f.sticky && f.inView, `${name} new order form: ActionBar pinned in view (bar=${f.bar} sticky=${f.sticky} inView=${f.inView})`)
      await shot('new-order')
    })
  }
  await browser.close()
}

const started = Date.now()
await run('phone', { width: 390, height: 844 })
await run('desktop', { width: 1440, height: 900 })
for (const n of notes) console.log(`ok    ${n}`)
for (const f of failures) console.log(`FAIL  ${f}`)
console.log(`${failures.length ? 'RESPONSIVE CHECK FAILED' : 'RESPONSIVE CHECK PASSED'}: ${notes.length} ok, ${failures.length} failed, ${((Date.now() - started) / 1000).toFixed(1)} s (${BASE})`)
process.exit(failures.length ? 1 : 0)
