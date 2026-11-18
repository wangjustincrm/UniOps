// @vitest-environment node
/**
 * The UI ratchet (scripts/ui-guard.mjs) as a test, so `npm test` in the shell
 * fails when a page adds hand-drawn controls, tables, overlays or raw colours.
 */
import { fileURLToPath } from 'node:url'
import { resolve, dirname } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const guardPath = resolve(here, '../../../scripts/ui-guard.mjs')

describe('ui-guard ratchet', () => {
  it('no page added hand-drawn UI since the baseline', async () => {
    const guard = await import(/* @vite-ignore */ guardPath)
    const bad = guard.regressions(guard.measure(), guard.loadBaseline())
    const lines = bad.map((b: { file: string; metric: string; allowed: number; now: number }) =>
      `${b.file}  ${b.metric}: ${b.allowed} -> ${b.now}`)
    expect(lines, 'Use @uniops/shell components / semantic tokens instead').toEqual([])
  })
  it('detects a raw button and a raw colour (positive control)', async () => {
    const guard = await import(/* @vite-ignore */ guardPath)
    const src = '<button className="bg-red-500 text-neutral-400">x</button>'
    expect(src.match(guard.METRICS['raw-control'])?.length).toBe(1)
    expect(src.match(guard.METRICS['raw-color'])?.length).toBe(2)
  })
  it('flags control chrome on a Shell Select (it lands on the wrapper)', async () => {
    const guard = await import(/* @vite-ignore */ guardPath)
    const re = guard.METRICS['select-chrome']
    expect('<Select className={inputCls} value={v}>'.match(re)?.length).toBe(1)
    expect('<Select className={fldCls(errors.x)}>'.match(re)?.length).toBe(1)
    expect('<Select className={`${INPUT} w-full`} value={v}>'.match(re)?.length).toBe(1)
    expect('<Select value={v}\n  className="h-10 rounded-lg border px-3">'.match(re)?.length).toBe(1)
    expect('<Select className="w-full" error={!!e} onChange={(e) => f(e)}>'.match(re)).toBeNull()
    expect('<Select className="w-40"><option /></Select><div className="border">'.match(re)).toBeNull()
  })
  it('a "/*" inside a string is not a comment (accept="image/*")', async () => {
    const guard = await import(/* @vite-ignore */ guardPath)
    const src = '<FileInput accept="image/*" />\n<button>x</button>\n/* gone */<input />'
    const stripped = guard.code(src)
    expect(stripped.match(guard.METRICS['raw-control'])?.length).toBe(2)
    expect(stripped).not.toContain('gone')
  })
})
