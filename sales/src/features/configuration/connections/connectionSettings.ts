import { useQuery } from '@tanstack/react-query'
import { fetchUsers } from '../notifications/notificationsApi'
import { connectionsClient, fetchEdiSettings, fetchSchedulerSettings } from './connectionsApi'

export const EDI_KEY = ['sales', 'config', 'edi-settings']
export const SCHEDULER_KEY = ['sales', 'config', 'scheduler']

export type AutomaticRuns = 'on' | 'off' | 'unavailable'

/** Automatic runs here: On, Off (switched off here) or not available on this server. */
export function useAutomaticRuns() {
  const q = useQuery({ queryKey: SCHEDULER_KEY, queryFn: fetchSchedulerSettings })
  const state: AutomaticRuns | undefined = !q.data ? undefined : !q.data.enabled ? 'unavailable' : q.data.paused ? 'off' : 'on'
  return { state, settings: q.data, error: q.error }
}

/** The EDI settings and the connections they can name (active SFTP ones). */
export function useEdiSettings() {
  const settings = useQuery({ queryKey: EDI_KEY, queryFn: fetchEdiSettings })
  const conns = useQuery({
    queryKey: [...EDI_KEY, 'sftp-connections'],
    queryFn: () => connectionsClient.list({ page: 1, pageSize: 100, filters: [{ field: 'kind', op: 'eq', value: 'SFTP' }, { field: 'active', op: 'eq', value: 'true' }], sort: [{ field: 'name' }] }),
  })
  const items = conns.data?.items ?? []
  const users = useQuery({ queryKey: [...EDI_KEY, 'users'], queryFn: () => fetchUsers('') })
  return {
    /** The operator's name: "No one" for none. */
    userName: (id: string | null | undefined) => (id == null ? 'No one' : users.data?.find((u) => u.id === id)?.name ?? '…'),
    settings: settings.data, error: settings.error ?? conns.error, loading: settings.isLoading || conns.isLoading,
    options: items.map((c) => ({ value: String(c.id), label: `${c.name} (write ${c.can_write ? 'on' : 'off'})` })),
    /** A connection's name by id: "None" for no id. */
    name: (id: number | null | undefined) => (id == null ? 'None' : items.find((c) => c.id === id)?.name ?? `#${id}`),
  }
}
