import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export interface PublicBranding {
  name: string
  tagline: string | null
  logo_data_url: string | null
  module?: string | null
}

/**
 * Company branding (name / tagline / logo) for a specific module.
 * `module` selects which tagline the backend resolves (default 'portal').
 * Name and logo are shared across all modules.
 */
export function useBranding(module = 'portal') {
  return useQuery<PublicBranding>({
    queryKey: ['public-branding', module],
    queryFn: () => epmsApi.get<PublicBranding>(`/config/public/branding?module=${module}`),
    staleTime: 5 * 60_000,
    retry: 1,
  })
}
