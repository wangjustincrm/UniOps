import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

/**
 * No page may download the whole invoice table (PGN-001, ISS-0127).
 *
 * `useInvoices()` with no filters pages through EVERY invoice (listAll): the
 * Invoices page did that on each open — 33 requests of 200 rows — only to count
 * statuses for its stat cards. Counts come from useInvoiceStatusCounts (the
 * server's `total`); filtered calls (one PO, one agreement) are fine.
 */
const SRC = path.resolve(__dirname, '..')

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) return walk(p)
    return /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [p] : []
  })
}

describe('invoice lists are paged by the server', () => {
  it('nothing calls useInvoices() without filters', () => {
    const offenders = walk(SRC).filter((f) => /\buseInvoices\(\s*\)/.test(fs.readFileSync(f, 'utf8')))
    expect(offenders.map((f) => path.relative(SRC, f))).toEqual([])
  })
})
