/**
 * Whether this person may open an incident's Section B (the medical part).
 *
 * HSE and first aiders always (ehs.incident.medical.read). ★ UAT 13, question
 * E: supervisors too, once HSE turn it on under Settings → Rules — so it is
 * the server's answer, not a permission key the page can read for itself.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function useSectionB(): boolean {
  const q = useQuery({
    queryKey: ['section-b-access'],
    queryFn: () => api.get<{ may_read: boolean }>('/api/v1/section-b/access'),
    staleTime: 5 * 60_000,
  })
  return q.data?.may_read === true
}
