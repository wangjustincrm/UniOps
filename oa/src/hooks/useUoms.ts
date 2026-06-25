import { useQuery } from '@tanstack/react-query'
import { mdmApi } from '@/lib/api'

interface ApiUom { id: string; code: string; name: string; dimension: string; is_active: boolean }
interface UomListResponse { items: ApiUom[]; total: number }

// Built-in fallback mirrors EPMS LINE_ITEM_UNITS (OA has no shared constant).
const FALLBACK_UNITS = ['pcs', 'kg', 'set', 'pair', 'box', 'carton', 'roll', 'm', 'm²', 'L', 'hour', 'month', 'lot']

export function useUomCodes(): string[] {
  const { data } = useQuery({
    queryKey: ['uoms', 'active'],
    queryFn: () => mdmApi.get<UomListResponse>('/uom?active_only=true'),
    staleTime: 60_000,
  })
  const codes = data?.items.map((u) => u.code) ?? []
  return codes.length > 0 ? codes : FALLBACK_UNITS
}
