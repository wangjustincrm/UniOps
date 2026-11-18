#!/usr/bin/env node
/**
 * UI ratchet guard — pages must draw with @uniops/shell, not by hand.
 *
 *   node scripts/ui-guard.mjs            check: exit 1 if any file got worse
 *   node scripts/ui-guard.mjs --update   lower the baseline to today's counts
 *                                         (never raises a count)
 *   node scripts/ui-guard.mjs --report   per-app totals, no check
 *   node scripts/ui-guard.mjs --adopt ehs/   admit today's counts for files under
 *                                         a path — ONLY when merging work that was
 *                                         written before the guard (a whole new
 *                                         module branch). Not for new code.
 *
 * For every .tsx under each app's src/ it counts the things that belong in the
 * Shell (raw controls, hand-made tables and overlays, non-token colours,
 * arbitrary sizes, shouting type, emoji). The baseline in
 * packages/shell/ui-guard-baseline.json records today's count per file and
 * metric. A file may only go DOWN. A new file starts at 0 for everything.
 *
 * Why a ratchet: the goal is "re-skin UniOps by editing packages/shell only".
 * Migrating ~2,700 call sites takes several releases; the ratchet makes sure
 * new feature work does not add new ones while that happens.
 */
import { readFileSync, writeFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { join, relative, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const BASELINE = join(ROOT, 'packages/shell/ui-guard-baseline.json')

export const METRICS = {
  // <button>, <input>, <select>, <textarea> written in a page
  'raw-control': /<(?:button|input|select|textarea)\b/g,
  // hand-built tables: use TableScroll/Table/THead/Th/Td
  'raw-table': /<(?:table|thead|tbody|tfoot|tr|th|td)\b/g,
  // home-made modal backdrops: use Modal / Drawer / ConfirmDialog
  'raw-overlay': /\bfixed inset-0\b/g,
  // colour outside the semantic tokens: raw Tailwind hues, raw neutral steps,
  // hex / rgb literals, arbitrary colour classes
  'raw-color': new RegExp(
    String.raw`(?<![\w-])(?:[\w\-\[\]&>=.:]+:)?(?:text|bg|border(?:-[trblxy])?|divide|ring|outline|fill|stroke|from|via|to|decoration|placeholder|shadow)-` +
    String.raw`(?:(?:red|rose|amber|yellow|orange|green|emerald|lime|blue|sky|teal|cyan|purple|violet|indigo|fuchsia|pink|gray|slate|zinc|stone|neutral)-\d{2,3}|\[#[0-9a-fA-F]{3,8}\]|\[rgba?\()` +
    String.raw`|["'\x60]#[0-9a-fA-F]{3,8}["'\x60]|\brgba?\(`, 'g'),
  // one-off sizes the type scale already has, hand-written shadows
  'arbitrary-size': /\b(?:text-\[\d+(?:\.\d+)?(?:px|rem)\]|shadow-\[[^\]]+\])/g,
  // UPPERCASE tracked labels and 700+ weights
  'shouting-type': /\b(?:uppercase|font-(?:bold|extrabold|black))\b/g,
  // Shell <Select> puts className on its wrapper: control chrome there (a border,
  // radius or height, or a style constant like inputCls / selectClass / fldCls())
  // draws a second box around the select. Only layout classes belong there.
  'select-chrome': new RegExp(
    String.raw`<Select\b(?:(?!</?[A-Za-z])[\s\S])*?className=(?:"[^"]*(?<![\w-])(?:border(?:-[\w-]+)?|rounded(?:-\w+)?|h-(?:9|10|11))(?![\w-])[^"]*"|\{[^}]*(?:[Cc]ls|Class|CLS)\b)`, 'g'),
  // emoji as icons / decoration
  'emoji': /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B50}\u{2705}\u{274C}\u{2B06}\u{27A1}]/gu,
}

/** Frontends = top-level dirs whose package.json depends on @uniops/shell. */
export function apps() {
  return readdirSync(ROOT).filter((d) => {
    const pkg = join(ROOT, d, 'package.json')
    if (!existsSync(pkg) || !existsSync(join(ROOT, d, 'src'))) return false
    try {
      const j = JSON.parse(readFileSync(pkg, 'utf8'))
      return !!({ ...j.dependencies, ...j.devDependencies })['@uniops/shell']
    } catch { return false }
  }).sort()
}

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '_retired' || name.startsWith('.')) continue
    const p = join(dir, name)
    const s = statSync(p)
    if (s.isDirectory()) walk(p, out)
    else if (/\.tsx$/.test(name) && !/\.(test|spec|stories)\.tsx$/.test(name)) out.push(p)
  }
  return out
}

/** Strip comments so prose like "the old <button>" does not count. */
export function code(src) {
  return src
    // A block comment starts after whitespace or punctuation, never mid-string:
    // accept="image/*" must not swallow the rest of the file.
    .replace(/(^|[\s{(,;=])\/\*[\s\S]*?\*\//g, '$1')
    .replace(/(^|[^:"'`\\])\/\/[^\n]*/g, '$1')
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '')
}

export function measure() {
  const result = {}
  for (const app of apps()) {
    for (const file of walk(join(ROOT, app, 'src'))) {
      const src = code(readFileSync(file, 'utf8'))
      const counts = {}
      for (const [k, re] of Object.entries(METRICS)) {
        const n = (src.match(re) || []).length
        if (n) counts[k] = n
      }
      if (Object.keys(counts).length) result[relative(ROOT, file).split('\\').join('/')] = counts
    }
  }
  return result
}

export function loadBaseline() {
  return existsSync(BASELINE) ? JSON.parse(readFileSync(BASELINE, 'utf8')).files ?? {} : {}
}

/** Files/metrics whose count rose above the baseline. */
export function regressions(current, baseline) {
  const out = []
  for (const [file, counts] of Object.entries(current)) {
    for (const [metric, n] of Object.entries(counts)) {
      const allowed = baseline[file]?.[metric] ?? 0
      if (n > allowed) out.push({ file, metric, allowed, now: n })
    }
  }
  return out
}

function totals(map) {
  const t = {}
  for (const [file, counts] of Object.entries(map)) {
    const app = file.split('/')[0]
    t[app] ??= {}
    for (const [k, n] of Object.entries(counts)) t[app][k] = (t[app][k] ?? 0) + n
  }
  return t
}

function writeBaseline(map) {
  const sorted = Object.fromEntries(Object.keys(map).sort().map((k) => [k, map[k]]))
  writeFileSync(BASELINE, JSON.stringify({
    $comment: 'UI ratchet baseline — counts may only go down. Regenerate with: node scripts/ui-guard.mjs --update',
    totals: totals(sorted),
    files: sorted,
  }, null, 1) + '\n')
}

function main(argv) {
  const current = measure()
  const baseline = loadBaseline()
  if (argv.includes('--report')) {
    console.table(totals(current))
    return 0
  }
  const adoptAt = argv.indexOf('--adopt')
  if (adoptAt >= 0) {
    const prefix = argv[adoptAt + 1]
    if (!prefix) { console.error('--adopt needs a path prefix, e.g. --adopt ehs/'); return 1 }
    const next = { ...baseline }
    let n = 0
    for (const [file, counts] of Object.entries(current)) {
      if (file.startsWith(prefix)) { next[file] = counts; n++ }
    }
    writeBaseline(next)
    console.log(`adopted ${n} files under ${prefix}`)
    return 0
  }
  if (argv.includes('--update')) {
    if (!existsSync(BASELINE) || argv.includes('--init')) { writeBaseline(current); console.log('baseline written'); return 0 }
    const bad = regressions(current, baseline)
    if (bad.length) { print(bad); console.error('\nRefusing to raise the baseline. Use Shell components instead.'); return 1 }
    // lower only: keep min(current, baseline); files now clean drop out
    const next = {}
    for (const [file, counts] of Object.entries(current)) next[file] = counts
    writeBaseline(next)
    console.log('baseline lowered')
    return 0
  }
  const bad = regressions(current, baseline)
  if (bad.length) { print(bad); return 1 }
  console.log(`ui-guard: OK (${Object.keys(current).length} files with legacy UI, none got worse)`)
  return 0
}

function print(bad) {
  console.error('ui-guard: these files added hand-drawn UI — use @uniops/shell components / semantic tokens:\n')
  for (const b of bad) console.error(`  ${b.file}  ${b.metric}: ${b.allowed} -> ${b.now}`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exit(main(process.argv.slice(2)))
}
