import type { ReactNode } from 'react'
import type { ViewsClient } from '../resource/viewsApi'
import { ViewsContext } from './views'

/** Gives every list below it its saved views (one client for the app). */
export function ViewsProvider({ client, children }: { client: ViewsClient; children: ReactNode }) {
  return <ViewsContext.Provider value={client}>{children}</ViewsContext.Provider>
}
