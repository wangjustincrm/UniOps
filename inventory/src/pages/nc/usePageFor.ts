import { useState } from 'react'

/** Page number that falls back to 1 whenever `filterKey` changes — derived
 *  during render instead of reset in an effect, so a filter change never
 *  fetches the old page number against the new filters first. */
export function usePageFor(filterKey: string): [number, (p: number) => void] {
  const [state, setState] = useState({ key: filterKey, page: 1 })
  const page = state.key === filterKey ? state.page : 1
  return [page, (p: number) => setState({ key: filterKey, page: p })]
}
