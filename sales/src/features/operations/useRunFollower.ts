import { useState } from 'react'
import { useNotices } from '@/platform-ui'
import { followRun, runNotice } from './followRun'

/** THE way a screen starts a run and follows it: `run(start)` starts it, says
 *  "Run 12 is running…" in the status row of the list or sheet it is in, polls
 *  it to its end, then says how it ended there, and returns the run. A refusal
 *  to start is thrown to the caller, which words it. `running` is true meanwhile. */
export function useRunFollower() {
  const { notify, clear } = useNotices()
  const [running, setRunning] = useState(false)
  const run = async (start: () => Promise<{ run_id: number }>) => {
    const { run_id } = await start()
    const key = `run:${run_id}`
    setRunning(true)
    notify({ key, tone: 'info', text: `Run ${run_id} is running…`, sticky: true })
    try {
      const ended = await followRun(run_id)
      notify({ key, ...runNotice(ended) })
      return ended
    } catch (e) {
      clear(key)
      throw e
    } finally {
      setRunning(false)
    }
  }
  return { run, running }
}
