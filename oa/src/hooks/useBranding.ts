import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export interface PublicBranding {
  name: string
  tagline: string | null
  logo_data_url: string | null
  module?: string | null
}

/** OA branding — shared company name/logo from epms-api, OA-specific tagline. */
export function useBranding() {
  return useQuery<PublicBranding>({
    queryKey: ['public-branding', 'oa'],
    queryFn: () => epmsApi.get<PublicBranding>('/config/public/branding?module=oa'),
    staleTime: 5 * 60_000,
    retry: 1,
  })
}
