import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export interface PublicBranding {
  name: string
  tagline: string | null
  logo_data_url: string | null
  module?: string | null
}

/** Order Bridge branding — shared company name/logo from epms-api, module-specific tagline. */
export function useBranding() {
  return useQuery<PublicBranding>({
    queryKey: ['public-branding', 'order_bridge'],
    queryFn: () => epmsApi.get<PublicBranding>('/api/v1/config/public/branding?module=order_bridge'),
    staleTime: 5 * 60_000,
    retry: 1,
  })
}
