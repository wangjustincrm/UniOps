import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export interface PublicBranding {
  name: string
  tagline: string | null
  logo_data_url: string | null
  module?: string | null
}

/** Inventory branding — shared company name/logo from epms-api, Inventory-specific tagline. */
export function useBranding() {
  return useQuery<PublicBranding>({
    queryKey: ['public-branding', 'inventory'],
    queryFn: () => epmsApi.get<PublicBranding>('/api/v1/config/public/branding?module=inventory'),
    staleTime: 5 * 60_000,
    retry: 1,
  })
}
