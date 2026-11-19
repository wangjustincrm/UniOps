// Accessibility for the responsive check (step 6, user 2026-11-18): axe-core (WCAG 2.0 / 2.1 / 2.2 A and AA) on a
// page or an open dialog, and what axe cannot see: a dialog takes the focus and gives it back. axe-core is installed
// beside playwright-core for the run only (A11Y_AXE: its axe.min.js); A11Y_DEBUG=1 adds each node's HTML and reason.
import fs from 'node:fs'

const AXE = process.env.A11Y_AXE ? fs.readFileSync(process.env.A11Y_AXE, 'utf8') : null
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']
export const axeReady = !!AXE

/** The serious and critical axe violations in `scope` (a CSS selector; default the whole page): "rule: target" each. */
export async function axeViolations(page, scope) {
  if (!AXE) return []
  if (!(await page.evaluate(() => 'axe' in window))) await page.addScriptTag({ content: AXE })
  const found = await page.evaluate(async ({ scope, tags, debug }) => {
    // Two parts of @uniops/shell (not Sales) are left out, reported to its owners: the tab strip and the pager.
    const r = await window.axe.run({ include: [scope ?? 'html'], exclude: [['[data-shell-tabbar]'], ['[data-shell-pagination]']] }, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations'] })
    return r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
      .flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(' ')}${debug ? ` <<${n.html.slice(0, 160)}>> ${(n.any[0]?.message ?? n.all[0]?.message ?? '').slice(0, 120)}` : ''}`))
  }, { scope, tags: TAGS, debug: !!process.env.A11Y_DEBUG })
  return [...new Set(found)]
}

/** The focus is inside the dialog on top. */
export const focusInDialog = (page) => page.evaluate(() => {
  const d = [...document.querySelectorAll('[role="dialog"]')].pop()
  return !!d && d.contains(document.activeElement)
})
