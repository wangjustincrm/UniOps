import { useQuery } from '@tanstack/react-query'
import { taskService, type TaskFilters } from '@/services/tasks'

// epms-api `/tasks` is shared across modules (Portal aggregates all of them,
// VMS pulls vms_* only). EPMS task pages should never show VMS rows, nor Direct
// PAs (`pa_dir`) which belong to OA and have no EPMS detail page — filter them
// client-side at the hook so all consumers (dashboard, inbox, etc.) inherit the
// exclusion without each having to remember. The shared API stays unfiltered so
// the Portal still receives `pa_dir` and deep-links it into OA.
const NON_EPMS_DOC_TYPES = new Set(['vms_visit', 'vms_train', 'vms_ppe', 'pa_dir'])

interface UseTasksOptions {
  /** Poll interval in ms. Only the Header sets this — see the note below. */
  refetchInterval?: number
}

/**
 * The ONE way to read /tasks in EPMS.
 *
 * The Header's unread badge used to call useQuery directly with the same key
 * (`['tasks', { is_completed: false }]`) but its own, UNFILTERED queryFn. Two
 * observers on one query key share a single cache entry and a single set of
 * options — whichever observer called setOptions last wins — so which queryFn
 * actually ran was nondeterministic, and on the wrong roll the Task Inbox
 * rendered the VMS / Direct-PA rows this hook exists to hide (`pa_dir` has no
 * entry in HREF_MAP, so its card deep-links to a bare "/<uuid>"). Seven call
 * sites shared that key. Giving the Header this options bag instead means one
 * key, one queryFn, and a badge count that matches the list it links to.
 */
export function useTasks(filters?: TaskFilters, options?: UseTasksOptions) {
  return useQuery({
    queryKey: ['tasks', filters],
    refetchInterval: options?.refetchInterval,
    queryFn: async () => {
      const resp = await taskService.list(filters)
      return {
        ...resp,
        items: resp.items.filter((t) => !NON_EPMS_DOC_TYPES.has(t.document_type)),
        total: resp.items.filter((t) => !NON_EPMS_DOC_TYPES.has(t.document_type)).length,
      }
    },
  })
}
