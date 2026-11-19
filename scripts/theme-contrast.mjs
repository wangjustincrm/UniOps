#!/usr/bin/env node
/**
 * Theme contrast check — the acceptance test for a UniOps theme.
 *
 *   node scripts/theme-contrast.mjs            every theme (default + themes/*.css)
 *   node scripts/theme-contrast.mjs <id>       one theme
 *
 * Resolves each theme's tokens (the @theme block in packages/shell/tokens.css,
 * overridden by :root[data-theme="<id>"] { … } in packages/shell/themes/<id>.css),
 * follows var(--…) chains, composites translucent colours over their background,
 * and checks every text-on-fill pair the Shell components actually draw against
 * WCAG 2.1: 4.5:1 for text, 3:1 for icons / large or non-text marks.
 * Exit 1 if any pair fails. See packages/shell/THEMING.md.
 */
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const SHELL = resolve(dirname(fileURLToPath(import.meta.url)), '../packages/shell')

function decls(block) {
  const out = {}
  for (const m of block.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].replace(/\/\*.*?\*\//g, '').trim()
  return out
}

function base() {
  const css = readFileSync(join(SHELL, 'tokens.css'), 'utf8')
  const i = css.indexOf('@theme {')
  let depth = 0, j = i
  for (; j < css.length; j++) { if (css[j] === '{') depth++; if (css[j] === '}' && --depth === 0) break }
  return decls(css.slice(i, j))
}

function themes() {
  const out = [{ id: 'uniops', vars: base(), file: 'tokens.css @theme' }]
  const dir = join(SHELL, 'themes')
  if (!existsSync(dir)) return out
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.css') && !f.startsWith('_') && f !== 'index.css')) {
    const css = readFileSync(join(dir, f), 'utf8')
    const m = css.match(/:root\[data-theme="([\w-]+)"\]\s*\{([\s\S]*?)\n\}/)
    if (!m) { console.error(`!! ${f}: no :root[data-theme="…"] { … } block`); process.exitCode = 1; continue }
    out.push({ id: m[1], vars: { ...out[0].vars, ...decls(m[2]) }, file: `themes/${f}` })
  }
  return out
}

function parse(v, vars, seen = 0) {
  v = v.trim()
  const ref = v.match(/^var\((--[\w-]+)\)$/)
  if (ref) { if (seen > 20 || !(ref[1] in vars)) return null; return parse(vars[ref[1]], vars, seen + 1) }
  let m = v.match(/^#([0-9a-f]{3,8})$/i)
  if (m) {
    let h = m[1]
    if (h.length <= 4) h = [...h].map((c) => c + c).join('')
    const n = (k) => parseInt(h.slice(k, k + 2), 16)
    return { r: n(0), g: n(2), b: n(4), a: h.length === 8 ? n(6) / 255 : 1 }
  }
  m = v.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:\s*[/,]\s*([\d.]+%?))?\s*\)$/)
  if (m) {
    const a = m[4] === undefined ? 1 : m[4].endsWith('%') ? parseFloat(m[4]) / 100 : parseFloat(m[4])
    return { r: +m[1], g: +m[2], b: +m[3], a }
  }
  return null
}

const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 })
const lum = ({ r, g, b }) => {
  const c = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4 }
  return 0.2126 * c(r) + 0.7152 * c(g) + 0.0722 * c(b)
}
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05) }
const hex = ({ r, g, b }) => '#' + [r, g, b].map((x) => Math.round(x).toString(16).padStart(2, '0')).join('').toUpperCase()

// [foreground token, background token, minimum, what draws it]
const TEXT = 4.5, MARK = 3
const PAIRS = [
  ['fg', 'surface', TEXT, 'body text'], ['fg', 'canvas', TEXT, 'page text'],
  ['fg-secondary', 'surface', TEXT, 'secondary text'], ['fg-secondary', 'surface-subtle', TEXT, 'table header'],
  ['fg-muted', 'surface', TEXT, 'muted text'], ['fg-muted', 'surface-subtle', TEXT, 'muted on hover row'],
  ['fg-muted', 'canvas', TEXT, 'muted on page'],
  ['fg-faint', 'surface', MARK, 'icons / placeholders'],
  ['accent-fg', 'surface', TEXT, 'links'], ['accent-fg', 'accent-soft', TEXT, 'accent badge'],
  ['fg-on-accent', 'accent', TEXT, 'primary button'], ['fg-on-accent', 'accent-hover', TEXT, 'primary button hover'],
  ['accent', 'surface', MARK, 'focus ring / checkbox'],
  ['fg-secondary', 'surface-sunken', TEXT, 'neutral badge'],
  ['fg-on-accent', 'fg-secondary', TEXT, 'dark badge (Paid)'],
  ...['success', 'warning', 'danger', 'info'].flatMap((s) => [
    [`${s}-fg`, `${s}-soft`, TEXT, `${s} badge / alert`], [`${s}-fg`, 'surface', TEXT, `${s} text`],
    [`${s}-solid`, 'surface', MARK, `${s} dot / icon`],
  ]),
  ...[1, 2, 3, 4, 5, 6, 7, 8].map((n) => [`cat${n}-fg`, `cat${n}-soft`, TEXT, `category ${n} badge`]),
  ['nav-fg', 'nav', TEXT, 'sidebar item'], ['nav-fg-active', 'nav', TEXT, 'sidebar active'],
  ['nav-fg-active', 'nav-item-active@nav', TEXT, 'sidebar active on highlight'],
  ['fg-on-inverse', 'inverse', TEXT, 'tooltip'], ['fg-on-inverse-muted', 'inverse-sunken', TEXT, 'viewer muted text'],
]

const want = process.argv[2]
let failed = 0
for (const t of themes().filter((t) => !want || t.id === want)) {
  const col = (name) => {
    const [tok, under] = name.split('@')
    const c = parse(`var(--color-${tok})`, t.vars)
    if (!c) return null
    if (c.a < 1) { const b = under ? col(under) : parse('var(--color-surface)', t.vars); return b ? over(c, b) : null }
    return c
  }
  console.log(`\n== ${t.id}  (${t.file})`)
  for (const [f, b, min, what] of PAIRS) {
    const bg = col(b), raw = parse(`var(--color-${f})`, t.vars)
    const fg = raw && bg ? (raw.a < 1 ? over(raw, bg) : raw) : null
    if (!fg || !bg) { console.log(`  ??  ${f} on ${b}: unresolved`); failed++; continue }
    const r = ratio(fg, bg), ok = r >= min
    if (!ok) failed++
    console.log(`  ${ok ? 'ok ' : 'FAIL'} ${r.toFixed(2).padStart(5)}:1 (≥${min})  ${f} ${hex(fg)} on ${b} ${hex(bg)} — ${what}`)
  }
}
console.log(failed ? `\n${failed} pair(s) below WCAG AA.` : '\nAll pairs pass WCAG AA.')
process.exit(failed ? 1 : 0)
