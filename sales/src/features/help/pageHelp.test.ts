import { describe, expect, it } from 'vitest'
import { EMPTY_TEXT, PAGE_HELP } from './pageHelp'

// The pages' help (round 3): every page id a screen names has its panel, and every panel is used.
const screens = import.meta.glob<string>('../**/*.tsx', { query: '?raw', import: 'default', eager: true })
const code = Object.entries(screens).filter(([f]) => !f.includes('.test.')).map(([, s]) => s).join('\n')

describe('page help', () => {
  it('every help="page:…" has a panel, and every panel is on a page', () => {
    const used = new Set([...code.matchAll(/help="(page:[^"]+)"/g)].map((m) => m[1]))
    expect([...used].sort()).toEqual(Object.keys(PAGE_HELP).sort())
  })
  it('panels and empty texts are short enough for a phone', () => {
    for (const [id, p] of Object.entries(PAGE_HELP)) {
      expect(p.body.length, id).toBeLessThanOrEqual(320)
      for (const t of p.tasks ?? []) expect(t.length, `${id}: ${t}`).toBeLessThanOrEqual(120)
    }
    for (const [k, t] of Object.entries(EMPTY_TEXT)) expect(t.length, k).toBeLessThanOrEqual(200)
  })
})
