// The search panel in a real browser (#32 stage 4, 2026-11-26), at a desktop width (1440 px) and a phone width (390 px):
// the table stays in sight beside the panel (desktop) / the panel takes the screen (phone); Tab stays inside the open panel;
// Search (n) is counted once after typing stops (the list's /count, never on each key); Esc closes and focus goes back to the
// Search icon; a record sheet opening closes the panel and keeps what was typed. Fails (exit 1) with what failed.
//
// Fully mocked like responsive.mjs (sales/e2e/fixtures; no token, no database). Run from the repository root against a running
// Vite of sales/ (BASE):
//
//   docker run --rm --network host --user "$(id -u)" -e HOME=/tmp -e BASE=http://127.0.0.1:5399 \
//     -v "$PWD/sales":/w -w /w mcr.microsoft.com/playwright:v1.50.0-noble \
//     sh -c 'npm i --prefix /tmp/pw --no-save --no-audit --no-fund playwright-core@1.50.0 axe-core@4.10.2 >/dev/null && \
//       PW=/tmp/pw/node_modules A11Y_AXE=/tmp/pw/node_modules/axe-core/axe.min.js node e2e/searchPanel.mjs'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import * as F from './fixtures/data.mjs'
import { axeReady, axeViolations } from './a11y.mjs'

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

const failures = []
const passed = []
const check = (ok, what) => (ok ? passed : failures).push(what)

function answer(url, counts) {
  const u = new URL(url)
  const p = u.pathname.replace(/^\/api\/v1\//, '')
  if (p === 'config/me/permissions') return { permissions: Object.fromEntries(PERMS.map((k) => [k, true])), roles: [] }
  if (p === 'config/public/branding') return { name: 'UniOps' }
  if (p === 'views' || p.startsWith('config/notification')) return []
  if (p === 'help/texts') return META['help/texts']
  if (p.endsWith('/meta')) return META[p.slice(0, -5)] ?? { version: 1, fields: [], views: [] }
  if (p === 'orders/count') { counts.push(u.search); return { total: 7 } }
  if (p === 'orders') return { items: F.orders, total: F.orders.length }
  const order = p.match(/^orders\/(\d+)$/)
  if (order) return F.orderDetail(Number(order[1]))
  return { items: [], total: 0 }
}

async function run(name, viewport) {
  const phone = name === 'phone'
  const browser = await chromium.launch({ executablePath: EXEC, headless: true })
  const ctx = await browser.newContext({ viewport, isMobile: phone, hasTouch: phone })
  await ctx.addInitScript((s) => localStorage.setItem('sales-auth', s), JSON.stringify(session))
  const counts = []
  await ctx.route('**/*', (route) => {
    const req = route.request()
    if (!new URL(req.url()).pathname.startsWith('/api/')) return route.continue()
    if (req.method() !== 'GET') return route.fulfill({ status: 409, contentType: 'application/json', body: '{"error":{"code":"e2e","message":"read only","details":{}}}' })
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(req.url(), counts)) })
  })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => failures.push(`${name}: page error ${String(e).slice(0, 200)}`))
  const shot = async (what) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: `${SHOTS}/${what}-${name}.png` }) } }
  const step = async (what, fn) => { try { await fn() } catch (e) { failures.push(`${name} ${what}: ${String(e.message ?? e).split('\n')[0]}`) } }
  const icon = page.getByRole('button', { name: /^Search on any field/ })
  const panel = page.getByRole('dialog', { name: 'Search on any field' })
  const inPanel = () => page.evaluate(() => !!document.activeElement?.closest('[role="dialog"][aria-label="Search on any field"]'))

  await page.goto(`${BASE}/orders`, { waitUntil: 'networkidle', timeout: 60000 })
  await page.locator(phone ? '[data-row-card]' : 'table tbody tr').first().waitFor({ timeout: 15000 })

  await step('opens beside the table', async () => {
    await icon.click()
    await panel.waitFor({ timeout: 5000 })
    const box = await panel.boundingBox()
    if (phone) {
      check(Math.round(box.x) === 0 && Math.round(box.width) === viewport.width, `${name}: the panel takes the whole width (${box.x}, ${box.width})`)
    } else {
      const cell = await page.locator('table tbody tr').first().locator('td').nth(1).boundingBox()
      check(box.width <= 400 && box.x > viewport.width / 2, `${name}: the panel is on the right (${Math.round(box.x)}, ${Math.round(box.width)} px)`)
      check(!!cell && cell.x + cell.width <= box.x, `${name}: the table's rows stay in sight beside it`)
      const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest('table') !== null, [cell.x + 5, cell.y + cell.height / 2])
      check(hit, `${name}: a row beside the panel can be clicked (nothing covers it)`)
    }
    if (axeReady) {
      const v = await axeViolations(page, '[role="dialog"][aria-label="Search on any field"]')
      check(v.length === 0, `${name}: the panel's accessibility (axe, WCAG A/AA)${v.length ? ': ' + v.join('; ') : ''}`)
    }
    await shot('open')
  })

  await step('Tab stays inside', async () => {
    let outside = 0
    for (let i = 0; i < 40; i += 1) { await page.keyboard.press('Tab'); if (!(await inPanel())) outside += 1 }
    for (let i = 0; i < 10; i += 1) { await page.keyboard.press('Shift+Tab'); if (!(await inPanel())) outside += 1 }
    check(outside === 0, `${name}: Tab and Shift+Tab stay inside the open panel (left it ${outside} times in 50)`)
  })

  await step('count after a pause', async () => {
    await panel.getByLabel('Find a field').fill('partner')
    await page.keyboard.press('Enter')                       // the one match: added
    const value = panel.getByLabel('Partner: value')
    await value.waitFor({ timeout: 5000 })
    const before = counts.length
    await value.pressSequentially('Sample', { delay: 60 })   // typing: no count per key
    await page.waitForTimeout(900)
    const asked = counts.slice(before)
    check(asked.length === 1, `${name}: one count after typing stops (asked ${asked.length} times: ${asked.join(' | ')})`)
    check(await panel.getByRole('button', { name: 'Search (7)' }).count() === 1, `${name}: the button says Search (7)`)
    await shot('count')
  })

  await step('Esc closes, focus to the icon', async () => {
    await panel.getByLabel('Partner: value').focus()
    await page.keyboard.press('Escape')
    await page.waitForTimeout(300)
    check(await panel.count() === 0, `${name}: Esc closes the panel`)
    const focused = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? '')
    check(focused.startsWith('Search on any field'), `${name}: focus back on the Search icon (on "${focused}")`)
  })

  if (!phone) {
    await step('a record sheet keeps the draft', async () => {
      await icon.click()
      await panel.getByLabel('Find a field').fill('partner')
      await page.keyboard.press('Enter')
      await panel.getByLabel('Partner: value').fill('Kept')
      await page.getByText('SMP-1030').first().click()               // an order beside the panel
      await page.getByRole('dialog', { name: /SMP-1030/ }).waitFor({ timeout: 10000 })
      check(await panel.count() === 0, `${name}: opening an order closes the panel`)
      await page.keyboard.press('Escape')
      await page.waitForTimeout(500)
      await icon.click()
      check(await panel.getByLabel('Partner: value').inputValue() === 'Kept', `${name}: the draft is kept for the next open`)
      await page.keyboard.press('Escape')
    })
  }
  await browser.close()
}

await run('desktop', { width: 1440, height: 900 })
await run('phone', { width: 390, height: 844 })
for (const p of passed) console.log(`ok    ${p}`)
for (const f of failures) console.log(`FAIL  ${f}`)
console.log(failures.length ? `${failures.length} failed, ${passed.length} passed` : `all ${passed.length} passed`)
process.exit(failures.length ? 1 : 0)
