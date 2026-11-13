import { useCallback, useSyncExternalStore } from 'react'

// Width from which the line items are a table instead of cards (ISS-0255).
export const LINE_TABLE_QUERY = '(min-width: 1024px)'

// Only ONE of the two layouts is rendered. Hiding the other with CSS left its
// inputs in the form: the browser still validates hidden inputs (min / step on
// the amounts), finds the hidden copy first, cannot focus it, and blocks Save
// with no message on a phone.
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    const mql = window.matchMedia(query)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches, () => true)
}
