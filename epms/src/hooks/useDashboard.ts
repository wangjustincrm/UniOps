import { useQuery } from '@tanstack/react-query'
import { dashboardService } from '@/services/dashboard'

export const dashboardQueryOptions = {
  queryKey: ['dashboard'],
  queryFn: () => dashboardService.get(),
  staleTime: 30_000,
  // DSH-010: a dashboard refreshes when the user comes back to the browser
  // tab (it does not auto-poll). The app turns refetch-on-focus off globally
  // (App.tsx) for forms; 'always' also ignores staleTime, so coming back a
  // few seconds after a change elsewhere still refetches (ISS-0139).
  refetchOnWindowFocus: 'always' as const,
}

export function useDashboard() {
  return useQuery(dashboardQueryOptions)
}
