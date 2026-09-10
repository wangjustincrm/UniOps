#!/usr/bin/env node
// Does every place the app navigates to actually resolve to a route?
//
// Written after OA shipped an Edit button that called
// navigate(`/expenses/edit/${id}`) against a route table that only had
// /expenses/:id. The tab rendered the shell's "No route matched" banner, and
// Return was a dead end for the whole module — an approver could send a claim
// back and nobody could change it. Nothing caught it: the target is a template
// string, so tsc has no opinion, and no test opened that page.
//
// This reads the real route table and the real navigate()/replaceTab() call
// sites out of src/, fills in :params, and asks react-router itself. Run it
// with `npm run check:routes`.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { matchRoutes } from 'react-router-dom'

const SRC = fileURLToPath(new URL('../src', import.meta.url))
const SAMPLE_ID = '11111111-2222-3333-4444-555555555555'

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    return statSync(p).isDirectory() ? walk(p) : p.endsWith('.tsx') || p.endsWith('.ts') ? [p] : []
  })
}

const files = walk(SRC)
const routeSrc = readFileSync(join(SRC, 'app/routes.tsx'), 'utf8')

const routes = [...routeSrc.matchAll(/\{\s*path:\s*'([^']+)'/g)]
  .map((m) => ({ path: m[1], element: null }))
if (!routes.length) {
  console.error('check-routes: found no paths in app/routes.tsx — has the table moved?')
  process.exit(2)
}

// navigate(...) / replaceTab(...) with a literal or template-literal argument.
// A computed target (a variable, a ternary) is skipped: there is nothing static
// to resolve, and guessing would produce noise rather than findings.
const targets = new Map()
for (const file of files) {
  const text = readFileSync(file, 'utf8')
  for (const m of text.matchAll(/(?:navigate|replaceTab)\(\s*[`'"]([^`'"]*)[`'"]/g)) {
    const raw = m[1]
    if (!raw.startsWith('/')) continue
    const filled = raw.replace(/\$\{[^}]*\}/g, SAMPLE_ID)
    if (!targets.has(filled)) targets.set(filled, file.slice(SRC.length + 1))
  }
}

let failures = 0
console.log(`${routes.length} routes, ${targets.size} navigation targets\n`)
for (const [target, where] of [...targets].sort()) {
  const m = matchRoutes(routes, target)
  const hit = m ? m[m.length - 1].route.path : null
  if (hit) {
    console.log(`  ok    ${target}  ->  ${hit}`)
  } else {
    failures++
    console.log(`  FAIL  ${target}  ->  NO ROUTE MATCHED   (${where})`)
  }
}

console.log(failures === 0
  ? '\nEvery navigation target resolves.'
  : `\n${failures} navigation target(s) resolve to nothing — the tab will show "No route matched".`)
process.exit(failures === 0 ? 0 : 1)
