// One-way dependency (design §13): nothing under src/platform-ui imports
// business code — not through '@/…' (except '@/platform-ui'), not through a
// relative path that climbs out of the folder. The ESLint rule covers the
// alias; this test resolves every import. Files are read through Vite's
// import.meta.glob, so the check needs no Node types in the app tsconfig.
import { describe, expect, it } from 'vitest'

const SELF = './importRule.test.ts'
const sources = import.meta.glob<string>('./**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true })
const libraryFiles = Object.keys(sources).filter((f) => f !== SELF)

const IMPORT = /(?:import|export)\s[^'"]*?from\s*['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)|^\s*import\s+['"]([^'"]+)['"]/gm

/** Resolve `spec` against `file` (both relative to this folder, './a/b.ts');
 *  true when the target is outside the folder. */
function escapes(file: string, spec: string): boolean {
  const parts = file.split('/').slice(1, -1) // drop '.' and the file name
  for (const seg of spec.split('/')) {
    if (seg === '..') {
      if (parts.length === 0) return true
      parts.pop()
    } else if (seg !== '.') {
      parts.push(seg)
    }
  }
  return false
}

export function businessImports(file: string, source: string): string[] {
  const bad: string[] = []
  for (const m of source.matchAll(IMPORT)) {
    const spec = m[1] ?? m[2] ?? m[3]
    if (spec.startsWith('@/')) {
      if (!/^@\/platform-ui(\/|$)/.test(spec)) bad.push(spec)
    } else if (spec.startsWith('.') && escapes(file, spec)) {
      bad.push(spec)
    }
  }
  return bad
}

describe('platform-ui import rule', () => {
  it('finds the library files', () => {
    expect(libraryFiles.length).toBeGreaterThanOrEqual(15)
  })

  it('no file imports business code', () => {
    const offenders = Object.fromEntries(
      libraryFiles
        .map((f) => [f, businessImports(f, sources[f])] as const)
        .filter(([, bad]) => bad.length > 0),
    )
    expect(offenders).toEqual({})
  })

  it('the checker flags what it must (positive half)', () => {
    const f = './data/X.tsx'
    expect(businessImports(f, "import { api } from '@/lib/api'")).toEqual(['@/lib/api'])
    expect(businessImports(f, "import x from '../../hooks/usePermissions'")).toEqual(['../../hooks/usePermissions'])
    expect(businessImports(f, "export { y } from '@/pages/Home'")).toEqual(['@/pages/Home'])
    expect(businessImports(f, "const m = import('@/store/auth')")).toEqual(['@/store/auth'])
    expect(businessImports(f, "import { cn } from '../lib/cn'")).toEqual([])
    expect(businessImports(f, "import { Dialog } from '@/platform-ui'")).toEqual([])
    expect(businessImports(f, "import { Button } from '@uniops/shell'")).toEqual([])
  })

  it('no module (Sales / Order Bridge) names inside the library', () => {
    // "sales" (case-insensitive) covers sales_, SALES_ and sales-api.
    const forbidden = /sales|sales_|sales-api|radiant|shopify|truecommerce|\b940\b/i
    expect(libraryFiles.filter((f) => forbidden.test(sources[f]))).toEqual([])
  })
})
