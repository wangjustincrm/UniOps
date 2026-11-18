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
  it('input-chrome: frame classes or a style constant on a Shell Input/Textarea; not layout, not bare', async () => {
    const guard = await import(/* @vite-ignore */ guardPath)
    const re = guard.METRICS['input-chrome']
    expect('<Input className={inputCls} value={v} />'.match(re)?.length).toBe(1)
    expect('<Input className={fldCls(errors.x)} />'.match(re)?.length).toBe(1)
    expect('<Textarea className={cn(INPUT_SM_CLS, "w-36")} />'.match(re)?.length).toBe(1)
    expect('<Input value={v}\n  className="h-8 rounded border px-2" />'.match(re)?.length).toBe(1)
    expect('<SearchInput className="bg-surface-sunken" />'.match(re)?.length).toBe(1)
    expect('<Input className={cn("w-40", err ? "border-danger-solid" : "")} />'.match(re)?.length).toBe(1)
    // allowed: layout / typography, state props, bare composite cells, MFA code box
    expect('<Input error={!!e} warning inputSize="sm" className="w-20 text-right font-mono" />'.match(re)).toBeNull()
    expect('<Input bare className="text-right px-2 py-1.5 focus:ring-1" />'.match(re)).toBeNull()
    // bare after an arrow-function prop: the ">" of "=>" must not end the attribute scan
    expect('<Input onChange={(e) => f(e)} bare className="rounded-lg" />'.match(re)).toBeNull()
    expect('<Input className="h-12 px-0 text-center font-mono text-lg" />'.match(re)).toBeNull()
    expect('<Input className={cn("pr-12", inputClassName)} />'.match(re)).toBeNull()
    expect('<Input className="w-40" /><div className="rounded border">'.match(re)).toBeNull()
  })
  it('hand-badge: a tinted span with badge padding, pill or square; not a chip, not layout', async () => {
    const guard = await import(/* @vite-ignore */ guardPath)
    const re = guard.METRICS['hand-badge']
    const hit = (src: string) => src.match(re)?.length ?? 0
    expect(hit('<span className="rounded-full bg-success-soft px-2 py-0.5 text-xs text-success-fg">Active</span>')).toBe(1)
    expect(hit('<span className="rounded bg-surface-strong px-1 py-0.5 text-2xs text-fg-muted">ignored</span>')).toBe(1)
    expect(hit("<span title={x} className={cn('rounded border px-1.5 py-0.5 text-2xs', st.cls)}>")).toBe(1)
    expect(hit("<span className={cn('rounded-full px-2 py-0.5', STATUS_STYLE[s])}>")).toBe(1)
    expect(hit('<div className="rounded-md bg-primary-50 px-2 py-0.5">x</div>')).toBe(1)
    // not badges
    expect(hit('<span data-chip key={u.id} className="rounded-full bg-primary-50 px-2 py-0.5">Ann <Button /></span>')).toBe(0)
    expect(hit('<div className="rounded-lg border bg-surface px-4 py-3">card</div>')).toBe(0)
    expect(hit('<span className="rounded px-1 py-0.5 font-mono text-xs">code</span>')).toBe(0)
    expect(hit('<Badge variant="success" size="sm">Active</Badge>')).toBe(0)
    // data-chip after an arrow-function prop still exempts
    expect(hit('<span onClick={() => f()} data-chip className="rounded-full bg-primary-50 px-2 py-0.5">x</span>')).toBe(0)
  })
  it('badge-chrome-string: badge chrome kept in a string constant', async () => {
    const guard = await import(/* @vite-ignore */ guardPath)
    const re = guard.METRICS['badge-chrome-string']
    const hit = (src: string) => src.match(re)?.length ?? 0
    expect(hit("const cls = 'shrink-0 inline-flex rounded-full px-1.5 py-0.5 text-2xs font-medium'")).toBe(1)
    expect(hit('const PILL = "rounded px-2 py-0.5 text-xs"')).toBe(1)
    expect(hit("const card = 'rounded-lg border bg-surface p-3'")).toBe(0)
    expect(hit("const row = 'rounded-md px-3 py-2 text-sm'")).toBe(0)
    expect(hit("const tone = 'success'")).toBe(0)
  })
  it('hand-card / card-chrome-string: a hand-drawn panel frame; not menus, state frames or Card', async () => {
    const guard = await import(/* @vite-ignore */ guardPath)
    const card = (src: string) => src.match(guard.METRICS['hand-card'])?.length ?? 0
    const str = (src: string) => src.match(guard.METRICS['card-chrome-string'])?.length ?? 0
    expect(card('<div className="rounded-xl border border-line bg-surface p-5">x</div>')).toBe(1)
    expect(card('<section className={cn("rounded-lg border bg-surface", open && "p-4")}>')).toBe(1)
    expect(card('<div className="absolute rounded-lg border bg-surface shadow-lg">menu</div>')).toBe(0)
    expect(card('<div className="rounded-lg border border-warning-line bg-surface p-3">!</div>')).toBe(0)
    expect(card('<div className="rounded-lg border bg-surface-subtle p-3">tinted</div>')).toBe(0)
    expect(card('<div data-panel className="rounded-xl border bg-surface">x</div>')).toBe(0)
    expect(card('<Card inset className="p-4">x</Card>')).toBe(0)
    expect(str("const panel = 'rounded-xl border border-line bg-surface p-6'")).toBe(1)
    expect(str("const menu = 'rounded-lg border bg-surface shadow-lg'")).toBe(0)
  })
  it('a "/*" inside a string is not a comment (accept="image/*")', async () => {
    const guard = await import(/* @vite-ignore */ guardPath)
    const src = '<FileInput accept="image/*" />\n<button>x</button>\n/* gone */<input />'
    const stripped = guard.code(src)
    expect(stripped.match(guard.METRICS['raw-control'])?.length).toBe(2)
    expect(stripped).not.toContain('gone')
  })
})
