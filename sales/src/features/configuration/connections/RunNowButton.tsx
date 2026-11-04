import { useQueryClient } from '@tanstack/react-query'
import { ActionButton, queryKeys } from '@/platform-ui'
import { runsClient } from '@/features/operations/operationsApi'
import { useRunFollower } from '@/features/operations/useRunFollower'
import { connectionsClient, runConnectionNow, type ConnectionRead } from './connectionsApi'

/** "Run now" for a connection in its sheet (sales.file.retry): followed to its
 *  end in the sheet's status row, then the list and the sheet's runs are refreshed. */
export function RunNowButton({ connection: c, variant }: { connection: ConnectionRead; variant?: 'primary' | 'secondary' }) {
  const qc = useQueryClient()
  const follower = useRunFollower()
  const runs = [...queryKeys.list(runsClient), 'connection', c.id]
  return (
    <ActionButton size="sm" variant={variant} permission="sales.file.retry" disabled={!c.active}
      errorMessages={{ 'run.already_running': `${c.name} is running; wait for it to finish.` }}
      onAction={async () => {
        await follower.run(async () => {
          const started = await runConnectionNow(c.id)
          await qc.invalidateQueries({ queryKey: runs })  // its table starts following it
          return started
        })
        await Promise.all([qc.invalidateQueries({ queryKey: queryKeys.list(connectionsClient) }), qc.invalidateQueries({ queryKey: runs })])
      }}>Run now</ActionButton>
  )
}
