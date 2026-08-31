import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export interface MyProfile {
  id: string
  email: string
  full_name: string
  role: string
  /** Base64 data URL, set on the EPMS profile page. Null until someone saves one. */
  signature_image: string | null
}

/**
 * The signed-in user's own profile, for the saved signature.
 *
 * Goes through epms-api's `/auth/me`, which proxies identity — the same path
 * the EPMS profile page writes through. Reaching identity directly would mean a
 * fourth base URL in this app, and a build arg that has been forgotten in a
 * Dockerfile three times already; the proxy costs one hop and no new plumbing.
 */
export function useMyProfile() {
  return useQuery<MyProfile>({
    queryKey: ['my-profile'],
    queryFn: () => epmsApi.get<MyProfile>('/api/v1/auth/me'),
    staleTime: 5 * 60_000,
    retry: 1,
  })
}
