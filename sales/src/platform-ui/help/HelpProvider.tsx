import type { ReactNode } from 'react'
import { HelpContext } from './context'
import type { HelpCatalog, HelpSource } from './types'

/** Gives the help texts to everything under it. `source` looks an id up; `onMissing` is told about an id
 *  with no text (a module warns in development; in production a missing text just shows no help);
 *  `catalog` lists every text, for the help panel's search. */
export function HelpProvider({ source, onMissing, catalog, children }: {
  source: HelpSource; onMissing?: (id: string) => void; catalog?: HelpCatalog; children: ReactNode
}) {
  return <HelpContext.Provider value={{ source, onMissing, catalog }}>{children}</HelpContext.Provider>
}
