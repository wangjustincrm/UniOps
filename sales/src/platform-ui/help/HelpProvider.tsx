import { useMemo, type ReactNode } from 'react'
import { HelpContext } from './context'
import type { HelpCatalog, HelpEntry, HelpSource } from './types'

/** Gives the help texts to everything under it, in layers: `layers` are plain text maps, a later one
 *  overriding an earlier one by id (e.g. [the component library's texts, the module's own]); `source`
 *  (texts that come from an API) overrides them all. `catalog` lists the source's texts for the search;
 *  the layers are listed by themselves. `onMissing` is told about an id with no text (warn in
 *  development; in production a missing text just shows no help). */
export function HelpProvider({ source, onMissing, catalog, layers, children }: {
  source?: HelpSource; onMissing?: (id: string) => void; catalog?: HelpCatalog
  layers?: Record<string, HelpEntry>[]; children: ReactNode
}) {
  const value = useMemo(() => {
    const merged: Record<string, HelpEntry> = Object.assign({}, ...(layers ?? []))
    return {
      source: (id: string) => source?.(id) ?? merged[id],
      onMissing,
      catalog: () => {
        const own = catalog?.() ?? []
        const ids = new Set(own.map((h) => h.id))
        return [...Object.entries(merged).filter(([id]) => !ids.has(id)).map(([id, entry]) => ({ id, entry })), ...own]
      },
    }
  }, [source, onMissing, catalog, layers])
  return <HelpContext.Provider value={value}>{children}</HelpContext.Provider>
}
