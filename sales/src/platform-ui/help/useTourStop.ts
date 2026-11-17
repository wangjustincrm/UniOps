import { useCallback, useContext } from 'react'
import { ScopeContext } from './tourContext'

/** Makes the element a tour stop of the nearest TourScope: put `attach` (as its ref) and `data-help` on it. `slot` is its
 *  place (a name in the slot table, never a number); `key` (default the slot): one stop per key; `text`: a
 *  text other than the element's own (a second tip on the same element). Outside a scope it only marks it. */
export function useTourStop({ help, slot, key, text }: { help: string; slot: string; key?: string; text?: string }) {
  const scope = useContext(ScopeContext)
  const ref = useCallback((el: HTMLElement | null) => {
    if (!scope || !el) return
    return scope.add({ help, slot, key: key ?? slot, el, text })
  }, [scope, help, slot, key, text])
  return { attach: ref, 'data-help': help }
}

/** The id of the nearest TourScope (e.g. a record's sheet names its scope after its list's). */
export const useScopeId = () => useContext(ScopeContext)?.id
