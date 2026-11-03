import type { ToastInput } from '@/platform-ui'
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

/** "Run 12: 3 files, 0 errors" — the toast a finished run gets. */
export function runToast(run: RunRead): ToastInput {
  const what = run.summary?.text ?? run.error ?? run.status
  return {
    title: `Run ${run.id}: ${what}`,
    description: run.status === 'SUCCEEDED' ? undefined : run.status,
    variant: run.status === 'SUCCEEDED' ? 'success' : run.status === 'RUNNING' ? 'info' : 'error',
  }
}
