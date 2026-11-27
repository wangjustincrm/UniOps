// Memory of kept-alive app tabs (user 2026-11-26, Part 1 c): the JS heap and the DOM nodes with 1 tab, then each tab opened up to
// 15 (Orders with 200 rows, Invoices, Inventory, …, record sheets open in some), measured after a garbage collection (CDP).
// Fully mocked like responsive.mjs. Prints one line per step and a JSON summary. Run like searchPanel.mjs (BASE = a Vite of sales/).
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import * as F from './fixtures/data.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(process.env.PW ? path.join(process.env.PW, 'x.js') : import.meta.url)
const { chromium } = require('playwright-core')
const BASE = (process.env.BASE ?? 'http://localhost:5182').replace(/\/$/, '')
const META = JSON.parse(fs.readFileSync(path.join(here, 'fixtures/meta.json'), 'utf8'))
const EXEC = process.env.CHROMIUM ?? fs.readdirSync('/ms-playwright').filter((d) => d.startsWith('chromium_headless_shell'))
  .map((d) => `/ms-playwright/${d}/chrome-linux/headless_shell`).find((p) => fs.existsSync(p))
const PERMS = ['sales.read', 'sales.order.operate', 'sales.invoice.manage', 'sales.inventory.adjust', 'sales.transfer.manage', 'sales.config', 'sales.settle']
const session = { state: { token: 'placeholder-not-a-token', refreshToken: null, isAuthenticated: true,
  user: { id: '00000000-0000-0000-0000-000000000001', email: 'e2e@example.invalid', full_name: 'E2E', role: 'system_admin', department_id: null } }, version: 0 }
const ORDERS200 = Array.from({ length: 200 }, (_, i) => ({ ...F.orders[i % F.orders.length], id: i + 1, order_number: 100000 + i,
  source_reference: `SMP-${2000 + i}` }))

function answer(url) {
  const u = new URL(url)
  const p = u.pathname.replace(/^\/api\/v1\//, '')
  if (p === 'config/me/permissions') return { permissions: Object.fromEntries(PERMS.map((k) => [k, true])), roles: [] }
  if (p === 'config/public/branding') return { name: 'UniOps' }
  if (p === 'views' || p.startsWith('config/notification')) return []
  if (p === 'help/texts') return META['help/texts']
  if (p.endsWith('/meta')) return META[p.slice(0, -5)] ?? { version: 1, fields: [], views: [] }
  if (p.endsWith('/count')) return { total: 200 }
  if (p === 'orders' || p === 'deliveries' || p === 'invoices') return { items: ORDERS200, total: ORDERS200.length }
  const order = p.match(/^orders\/(\d+)$/)
  if (order) return F.orderDetail(Number(order[1]))
  if (p === 'payments') return { items: F.payments, total: F.payments.length, totals: F.totalsOf(F.payments) }
  if (p === 'inventory/items') return { items: F.stockItems, total: F.stockItems.length }
  if (p === 'inventory/lots') return { items: F.stockLots('ITEM-1'), total: 2 }
  if (p === 'operations/health') return F.health
  if (p === 'operations/dashboard') return F.dashboard
  if (p === 'config/partners') return { items: F.partners, total: F.partners.length }
  if (p === 'config/items') return { items: F.items, total: F.items.length }
  return { items: [], total: 0 }
}

const ROUTES = ['/orders', '/settlements', '/audit-log', '/config/connections', '/invoices', '/deliveries', '/payments', '/inventory', '/lots', '/transfers', '/documents', '/runs', '/health',
  '/notifications', '/config/partners', '/config/items', '/config/partner-codes', '/config/dc-routing', '/config/warehouses', '/config/carriers']

const browser = await chromium.launch({ executablePath: EXEC, headless: true, args: ['--enable-precise-memory-info', '--js-flags=--expose-gc'] })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
await ctx.addInitScript((s) => localStorage.setItem('sales-auth', s), JSON.stringify(session))
await ctx.addInitScript(() => localStorage.setItem('platform-ui.autoRefresh./orders', '30'))
await ctx.route('**/*', (route) => {
  const req = route.request()
  if (!new URL(req.url()).pathname.startsWith('/api/')) return route.continue()
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(req.url())) })
})
const page = await ctx.newPage()
const cdp = await ctx.newCDPSession(page)
await cdp.send('Performance.enable')
const reqs = []
page.on('request', (r) => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/')) reqs.push(u.pathname) })
const measure = async (label) => {
  await page.waitForTimeout(600)
  await cdp.send('HeapProfiler.collectGarbage')
  const m = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map((x) => [x.name, x.value]))
  const tabs = await page.evaluate(() => document.querySelectorAll('main > div > div').length)  // TabHost: one div per mounted tab
  const row = { label, heapMB: +(m.JSHeapUsedSize / 1048576).toFixed(1), nodes: m.Nodes, listeners: m.JSEventListeners, mountedTabs: tabs }
  console.log(JSON.stringify(row))
  return row
}
await page.goto(`${BASE}/home`, { waitUntil: 'networkidle', timeout: 60000 })
const steps = [await measure('home only')]
for (const r of ROUTES) {
  // In-app navigation (the router, not a page load): the shell opens or focuses the route's tab.
  await page.evaluate((to) => { window.history.pushState({}, '', to); window.dispatchEvent(new PopStateEvent('popstate')) }, r)
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(400)
  if (r === '/orders' || r === '/invoices') {
    await page.locator('main [role="tabpanel"], main').locator('table tbody tr').first().click().catch(() => {})
    await page.waitForTimeout(500)
    await page.keyboard.press('Escape').catch(() => {})
  }
  steps.push(await measure(`+ ${r}`))
}
// Stage 4 (user 2026-11-27): 20 record tabs on top — the shell's own cap is out of reach now (no list tab is ever evicted);
// Sales keeps 12 record tabs, the oldest unchanged one closed with a notice and Reopen.
const tabTitles = () => page.locator('[data-shell-tabbar] [role="tab"]').allInnerTexts()
const listsBefore = (await tabTitles()).length
const failures = []
for (let i = 1; i <= 20; i++) {
  await page.evaluate((to) => { window.history.pushState({}, '', to); window.dispatchEvent(new PopStateEvent('popstate')) }, `/orders/${i}`)
  // As a person does: the next one once this one's tab is shown (a navigation made before the last has rendered is
  // dropped by the router's transition, which no click can do).
  await page.waitForFunction((n) => [...document.querySelectorAll('[data-shell-tabbar] [role="tab"][aria-selected="true"]')]
    .some((t) => t.textContent.includes(`#${n}`) || t.textContent.includes(`SMP-${1000 + n}`)), i, { timeout: 10000 })
  if (i % 5 === 0) steps.push(await measure(`+ ${i} record tabs`))
}
await page.waitForLoadState('networkidle')
const titles = (await tabTitles()).map((t) => t.trim())
const records = titles.length - listsBefore
const notice = await page.locator('[data-shell-tabbar] [role="status"]').innerText().catch(() => '')
console.log(JSON.stringify({ listTabs: listsBefore, recordTabs: records, notice, titles }))
if (records !== 12) failures.push(`12 record tabs kept, not ${records}`)
if (titles.length - records !== listsBefore) failures.push('a list tab was closed')
if (!/closed to keep 12 open/.test(notice)) failures.push(`the cap's notice (${notice})`)

// The ticks of kept-alive tabs: requests in 70 s while one tab (the last) is shown.
const before = reqs.length
await page.waitForTimeout(Number(process.env.IDLE_MS ?? 70000))
const idle = { requestsIn70s: reqs.length - before, what: reqs.slice(before) }
console.log(JSON.stringify(idle))
console.log('SUMMARY ' + JSON.stringify({ first: steps[0], last: steps[steps.length - 1], idle }))
await browser.close()
if (failures.length) { console.log('FAILED:\n' + failures.map((x) => `  - ${x}`).join('\n')); process.exit(1) }
