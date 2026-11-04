import type { NoticeInput } from '@/platform-ui'
import { runsClient, type RunRead } from './operationsApi'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** Waits for a started run to end (it is polled), then returns it. */
export async function followRun(id: number, { every = 1500, give_up_after = 600_000 } = {}): Promise<RunRead> {
  for (let waited = 0; ; waited += every) {
    const run = await runsClient.get(id)
    if (run.status !== 'RUNNING' || waited >= give_up_after) return run
    await sleep(every)
  }
}

/** "Run 12: 3 files, 0 errors" (a run that did not simply succeed says how it
 *  ended first) — what the status row says about a finished run. */
export function runNotice(run: RunRead): NoticeInput {
  const what = run.summary?.text ?? run.error
  if (run.status === 'RUNNING') return { tone: 'info', text: `Run ${run.id} is still running` }
  if (run.status === 'SUCCEEDED') return { tone: 'success', text: `Run ${run.id}: ${what ?? run.status}` }
  return { tone: run.status === 'PARTIAL' ? 'warning' : 'error', text: `Run ${run.id}: ${run.status}${what ? ` — ${what}` : ''}` }
}
