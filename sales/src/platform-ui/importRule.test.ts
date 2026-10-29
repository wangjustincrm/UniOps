// One-way dependency (design §13): nothing under src/platform-ui imports
// business code — not through '@/…' (except '@/platform-ui'), not through a
// relative path that climbs out of the folder. And the layers inside it
// (design §13.15):
//   primitives → primitives, lib, @uniops/shell (no API / query code)
//   resource   → resource, lib                  (no @uniops/shell)
//   patterns   → patterns, primitives, resource, lib (no @uniops/shell)
//   lib        → lib
// ESLint gives the same answers; this test resolves every relative import.
// Files are read through Vite's import.meta.glob, so the check needs no Node
// types in the app tsconfig.
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

type Layer = 'primitives' | 'patterns' | 'resource' | 'lib' | 'root'
const LAYERS = ['primitives', 'patterns', 'resource', 'lib'] as const
const MAY_IMPORT: Record<Exclude<Layer, 'root'>, Layer[]> = {
  primitives: ['primitives', 'lib'],
  resource: ['resource', 'lib'],
  patterns: ['patterns', 'primitives', 'resource', 'lib'],
  lib: ['lib'],
}
const BANNED_PACKAGES: Record<Exclude<Layer, 'root'>, RegExp | null> = {
  primitives: /^@tanstack\/react-query$/,
  resource: /^@uniops\/shell(\/|$)/,
  patterns: /^@uniops\/shell(\/|$)/,
  lib: /^@uniops\/shell(\/|$)/,
}

function layerOfPath(parts: string[]): Layer {
  return (LAYERS as readonly string[]).includes(parts[0]) && parts.length > 1 ? (parts[0] as Layer) : 'root'
}

/** The layer an import lands in ('./a/b.ts' + '../x/y'), or null outside. */
function target(file: string, spec: string): Layer | null {
  const parts = file.split('/').slice(1, -1)
  for (const seg of spec.split('/')) {
    if (seg === '..') {
      if (parts.length === 0) return null
      parts.pop()
    } else if (seg !== '.') {
      parts.push(seg)
    }
  }
  return layerOfPath([...parts, 'x'])
}

export function layerViolations(file: string, source: string): string[] {
  const own = layerOfPath(file.split('/').slice(1))
  if (own === 'root') return []
  const bad: string[] = []
  for (const m of source.matchAll(IMPORT)) {
    const spec = m[1] ?? m[2] ?? m[3]
    let to: Layer | null = null
    if (spec.startsWith('.')) to = target(file, spec)
    else if (spec.startsWith('@/platform-ui/')) to = layerOfPath([...spec.slice('@/platform-ui/'.length).split('/'), 'x'])
    else if (spec === '@/platform-ui') to = 'root'
    if (to !== null && !MAY_IMPORT[own].includes(to)) bad.push(spec)
    else if (BANNED_PACKAGES[own]?.test(spec)) bad.push(spec)
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

  it('each layer imports only what it may', () => {
    const offenders = Object.fromEntries(
      libraryFiles
        .map((f) => [f, layerViolations(f, sources[f])] as const)
        .filter(([, bad]) => bad.length > 0),
    )
    expect(offenders).toEqual({})
    // The walk really covers all three layers.
    for (const layer of ['primitives', 'patterns', 'resource']) {
      expect(libraryFiles.filter((f) => f.startsWith(`./${layer}/`)).length).toBeGreaterThan(2)
    }
  })

  it('the layer checker flags what it must (positive half)', () => {
    const prim = './primitives/X.tsx'
    expect(layerViolations(prim, "import { useCan } from '../patterns/permissions'")).toEqual(['../patterns/permissions'])
    expect(layerViolations(prim, "import { errorText } from '../resource/businessError'")).toEqual(['../resource/businessError'])
    expect(layerViolations(prim, "import { useQuery } from '@tanstack/react-query'")).toEqual(['@tanstack/react-query'])
    expect(layerViolations(prim, "import { x } from '@/platform-ui/patterns/QueryTable'")).toEqual(['@/platform-ui/patterns/QueryTable'])
    expect(layerViolations(prim, "import { Button } from '@uniops/shell'")).toEqual([])
    expect(layerViolations(prim, "import { cn } from '../lib/cn'")).toEqual([])
    expect(layerViolations(prim, "import { Popover } from './Popover'")).toEqual([])
    const res = './resource/X.ts'
    expect(layerViolations(res, "import { Button } from '@uniops/shell'")).toEqual(['@uniops/shell'])
    expect(layerViolations(res, "import { Dialog } from '../primitives/Dialog'")).toEqual(['../primitives/Dialog'])
    expect(layerViolations(res, "import { toSearchParams } from './querySpec'")).toEqual([])
    const pat = './patterns/X.tsx'
    expect(layerViolations(pat, "import { Button } from '@uniops/shell'")).toEqual(['@uniops/shell'])
    expect(layerViolations(pat, "import { Button } from '../primitives/shell'")).toEqual([])
    expect(layerViolations(pat, "import { errorText } from '../resource/businessError'")).toEqual([])
    expect(layerViolations(pat, "import { x } from '..'")).toEqual(['..'])
  })

  it('no module (Sales / Order Bridge) names inside the library', () => {
    // "sales" (case-insensitive) covers sales_, SALES_ and sales-api.
    const forbidden = /sales|sales_|sales-api|radiant|shopify|truecommerce|\b940\b/i
    expect(libraryFiles.filter((f) => forbidden.test(sources[f]))).toEqual([])
  })
})
