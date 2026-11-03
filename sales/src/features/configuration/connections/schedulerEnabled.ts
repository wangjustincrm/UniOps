import { useListMeta } from '@/platform-ui'
import { connectionsClient } from './connectionsApi'

/** Whether the scheduler runs here (the Connections list's meta says; until it is known, assume it does). */
export function useSchedulerEnabled(): boolean {
  const meta = useListMeta(connectionsClient)
  return (meta.data as { scheduler_enabled?: boolean } | undefined)?.scheduler_enabled !== false
}
