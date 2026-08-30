import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export interface PublicBranding {
  name: string
  tagline: string | null
  logo_data_url: string | null
  module?: string | null
}

/** Safety branding — shared company name and logo from epms-api. */
export function useBranding() {
  return useQuery<PublicBranding>({
    queryKey: ['public-branding', 'ehs'],
    queryFn: () => epmsApi.get<PublicBranding>('/api/v1/config/public/branding?module=ehs'),
    staleTime: 5 * 60_000,
    retry: 1,
  })
}
