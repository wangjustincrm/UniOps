import { useQuery } from '@tanstack/react-query'
import { connectionsClient, fetchEdiSettings, fetchSchedulerSettings, fetchShadowSetting } from './connectionsApi'

export const EDI_KEY = ['sales', 'config', 'edi-settings']
export const SHADOW_KEY = ['sales', 'config', 'shadow']
export const SCHEDULER_KEY = ['sales', 'config', 'scheduler']

/** The shadow run's setting (on or off, and while off what switching it on would mark). */
export const useShadowSetting = () => useQuery({ queryKey: SHADOW_KEY, queryFn: fetchShadowSetting })

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
  return {
    settings: settings.data, error: settings.error ?? conns.error, loading: settings.isLoading || conns.isLoading,
    options: items.map((c) => ({ value: String(c.id), label: `${c.name} (write ${c.can_write ? 'on' : 'off'})` })),
    /** A connection's name by id: "None" for no id. */
    name: (id: number | null | undefined) => (id == null ? 'None' : items.find((c) => c.id === id)?.name ?? `#${id}`),
  }
}
