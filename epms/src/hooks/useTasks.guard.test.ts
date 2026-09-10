import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

/**
 * `['tasks', …]` must have exactly ONE queryFn.
 *
 * The Header's unread badge used to call useQuery directly with the key
 * `['tasks', { is_completed: false }]` — byte-identical to what
 * `useTasks({ is_completed: false })` hashes to — but with its own queryFn that
 * did NOT filter out the VMS / Direct-PA rows EPMS has no pages for. React
 * Query keeps one options set per query key (last observer to call setOptions
 * wins), so which of the two queryFns actually ran was a race, and on the wrong
 * roll the Task Inbox rendered a `pa_dir` card whose taskHref falls through to
 * a bare "/<uuid>" dead link. Seven call sites shared that key.
 *
 * A source guard rather than a behavioural test on purpose: the failure mode is
 * "someone adds a second queryFn on this key months from now", which no runtime
 * assertion in a single test can observe.
 */

const SRC = path.resolve(__dirname, '..')
const OWNER = path.join(SRC, 'hooks', 'useTasks.ts')

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) return walk(p)
    return /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [p] : []
  })
}

describe("the ['tasks'] query key", () => {
  it('is only ever fetched through the useTasks hook', () => {
    const offenders = walk(SRC).filter((file) => {
      if (file === OWNER) return false
      const src = fs.readFileSync(file, 'utf8')
      // A useQuery/useSuspenseQuery call whose queryKey array starts with 'tasks'.
      return /use(?:Suspense)?Query\s*\(\s*\{[^}]*queryKey:\s*\[\s*['"]tasks['"]/s.test(src)
    })
    expect(
      offenders.map((f) => path.relative(SRC, f)),
      "these files fetch ['tasks'] with their own queryFn — call useTasks() instead " +
        '(it takes a refetchInterval option for the Header badge)',
    ).toEqual([])
  })

  it('exposes the refetchInterval escape hatch the Header needs', () => {
    const src = fs.readFileSync(OWNER, 'utf8')
    expect(src).toMatch(/refetchInterval/)
    expect(src).toMatch(/export function useTasks\(/)
  })

  it('still hides the document types EPMS has no page for', () => {
    const src = fs.readFileSync(OWNER, 'utf8')
    // pa_dir is the one that mattered: HREF_MAP has no entry, so its card
    // deep-links to "/<uuid>" and dead-ends.
    for (const docType of ['vms_visit', 'vms_train', 'vms_ppe', 'pa_dir']) {
      expect(src).toContain(`'${docType}'`)
    }
  })
})
