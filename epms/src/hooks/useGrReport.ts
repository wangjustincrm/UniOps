import { useQuery } from '@tanstack/react-query'
import { grReportService, type ReceivingReportFilters } from '@/services/grReport'

export function useReceivingReport(filters: ReceivingReportFilters) {
  return useQuery({
    queryKey: ['gr-receiving-report', filters],
    queryFn: () => grReportService.receiving(filters),
    staleTime: 30_000,
    // Keep the previous page on screen while the next one loads. Without it
    // every page turn unmounts the table for the empty state and back again —
    // the header, the scroll position and the scrollbar all jump.
    placeholderData: (previous) => previous,
  })
}
