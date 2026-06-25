import { useQuery } from '@tanstack/react-query'
import { uomService } from '@/services/uom'
import { LINE_ITEM_UNITS } from '@/types'

/**
 * Active UOM codes for line-item dropdowns. Falls back to the built-in
 * LINE_ITEM_UNITS constant while loading or if the master list is empty.
 */
export function useUomCodes(): string[] {
  const { data } = useQuery({
    queryKey: ['uoms', 'active'],
    queryFn: () => uomService.list(true),
    staleTime: 60_000,
  })
  const codes = data?.items.map((u) => u.code) ?? []
  return codes.length > 0 ? codes : [...LINE_ITEM_UNITS]
}
