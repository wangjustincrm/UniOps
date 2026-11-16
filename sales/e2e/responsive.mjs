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
  if (p === 'views') return []
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
  if (p === 'config/partners') return { items: F.partners, total: F.partners.length }
  if (p === 'config/items') return { items: F.items, total: F.items.length }
  return { items: [], total: 0 }
}

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
    await page.getByRole('tab', { name: /Lines/ }).click()
    await page.getByRole('dialog').getByRole('button', { name: /Show details/ }).first().click()
    await page.waitForTimeout(300)
    const l = await measure(page)
    check(l.sheet <= 0, `${name} order lines with packing: sideways overflow ${l.sheet}px`)
    await shot('order-lines')
    await page.keyboard.press('Escape')
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
    await listChecks('inventory-lots')
  })
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
