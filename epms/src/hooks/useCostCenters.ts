/**
 * Cost Centers — read-only hook for EPMS pages (PR create/edit, Budget, Reports).
 *
 * Writes (create/update/delete) have moved to Portal → Finance → Budget Config,
 * which calls mdm-api directly. Use that surface, not this hook.
 */
import { useQuery } from '@tanstack/react-query'
import { costCenterService, type CostCenterFilters } from '@/services/cost_centers'

export function useCostCenters(filters?: CostCenterFilters) {
  return useQuery({
    queryKey: ['cost-centers', filters],
    queryFn: () => costCenterService.list(filters),
  })
}
