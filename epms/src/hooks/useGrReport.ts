import { useQuery } from '@tanstack/react-query'
import { grReportService, type ReceivingReportFilters } from '@/services/grReport'

export function useReceivingReport(filters: ReceivingReportFilters) {
  return useQuery({
    queryKey: ['gr-receiving-report', filters],
    queryFn: () => grReportService.receiving(filters),
    staleTime: 30_000,
  })
}
