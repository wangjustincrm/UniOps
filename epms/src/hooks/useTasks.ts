import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { taskService, type TaskFilters } from '@/services/tasks'

// epms-api `/tasks` is shared across modules (Portal aggregates all of them,
// VMS pulls vms_* only). EPMS task pages should never show VMS rows, nor Direct
// PAs (`pa_dir`) which belong to OA and have no EPMS detail page — filter them
// client-side at the hook so all consumers (dashboard, inbox, etc.) inherit the
// exclusion without each having to remember. The shared API stays unfiltered so
// the Portal still receives `pa_dir` and deep-links it into OA.
const NON_EPMS_DOC_TYPES = new Set(['vms_visit', 'vms_train', 'vms_ppe', 'pa_dir'])

export function useTasks(filters?: TaskFilters) {
  return useQuery({
    queryKey: ['tasks', filters],
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

export function useCompleteTask() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => taskService.complete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}
