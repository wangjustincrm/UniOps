import { useQueryClient } from '@tanstack/react-query'
import { Pause, Play, RefreshCw } from 'lucide-react'
import { RowActions, businessErrorOf, queryKeys, useNotices } from '@/platform-ui'
import { useRunFollower } from '@/features/operations/useRunFollower'
import { connectionsClient, insideWindow, runConnectionNow, type ConnectionRead } from './connectionsApi'

/** A connection's row in the list: ⏸ Pause / ▶ Resume its schedule
 *  (sales.config; an audited, versioned update — resuming outside its active
 *  hours asks first) and ⟳ Run now (sales.file.retry; followed to its end).
 *  What happened is said in the list's status row. Shown only with the
 *  permission; an inactive connection has no Run now. */
export function ConnectionRowActions({ connection: c }: { connection: ConnectionRead }) {
  const qc = useQueryClient()
  const { notify } = useNotices()
  const follower = useRunFollower()
  const refresh = () => qc.invalidateQueries({ queryKey: queryKeys.list(connectionsClient) })
  const fail = (e: unknown, what: string) => {
    const be = businessErrorOf(e)
    notify({ tone: 'error', text: be?.code === 'run.already_running'
      ? `${c.name} is running; wait for it to finish.` : be?.code === 'entity.stale'
        ? 'Someone else changed this connection; the list is refreshed.' : `${what}: ${c.name} — ${be?.message ?? String(e)}` })
  }
  const setPaused = async (paused: boolean) => {
    try {
      await connectionsClient.update(c.id, { schedule_paused: paused }, c.version)
      notify({ tone: 'success', text: `${c.name}: schedule ${paused ? 'paused' : 'resumed'}` })
    } catch (e) {
      fail(e, paused ? 'Not paused' : 'Not resumed')
    }
    await refresh()
  }
  const runNow = async () => {
    try {
      await follower.run(() => runConnectionNow(c.id))
    } catch (e) {
      fail(e, 'Not run')
    }
    await refresh()
  }
  return (
    <RowActions actions={[
      c.schedule_paused
        ? { key: 'resume', label: 'Resume schedule', icon: <Play className="h-4 w-4" />, permission: 'sales.config',
            onAction: () => setPaused(false),
            confirm: () => (insideWindow(c, Date.now()) ? null : { title: `Resume ${c.name}?`, confirmLabel: 'Resume',
              description: 'It is outside its active hours now: it runs when the window opens (Run now runs it at once).' }) }
        : { key: 'pause', label: 'Pause schedule', icon: <Pause className="h-4 w-4" />, permission: 'sales.config',
            onAction: () => setPaused(true) },
      { key: 'run', label: 'Run now', icon: <RefreshCw className="h-4 w-4" />, permission: 'sales.file.retry',
        hidden: !c.active, onAction: runNow },
    ]} />
  )
}
