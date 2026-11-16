import type { ReactNode } from 'react'
import { HelpContext } from './context'
import type { HelpSource } from './types'

/** Gives the help texts to everything under it. `source` looks an id up; `onMissing` is told about an id
 *  with no text (a module warns in development; in production a missing text just shows no help). */
export function HelpProvider({ source, onMissing, children }: { source: HelpSource; onMissing?: (id: string) => void; children: ReactNode }) {
  return <HelpContext.Provider value={{ source, onMissing }}>{children}</HelpContext.Provider>
}
