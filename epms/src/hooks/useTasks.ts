import { useQuery } from '@tanstack/react-query'
import { taskService, type TaskFilters } from '@/services/tasks'
import { EPMS_PAGE_DOC_TYPES } from '@/lib/taskTypes'

// epms-api `/tasks` is shared across modules (Portal aggregates all of them,
// VMS pulls vms_* only). Keep only the doc types an EPMS page acts on
// (EPMS_PAGE_DOC_TYPES — an allow-list, see taskTypes.ts) here at the hook so
// every consumer inherits it. That set still includes budget_plan for the
// embedded plan editor's Approve gate; the inbox surfaces (Task Inbox page,
// header bell, dashboard list) narrow further with isEpmsInboxTask. The shared
// API stays unfiltered so Portal still receives everything.

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
      const items = resp.items.filter((t) => EPMS_PAGE_DOC_TYPES.has(t.document_type.toLowerCase()))
      return { ...resp, items, total: items.length }
    },
  })
}
