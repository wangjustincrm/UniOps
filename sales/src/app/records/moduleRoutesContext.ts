import { createContext, useContext } from 'react'
import type { RouteDef } from '@uniops/shell'

/** The module's route table, for the record pages' Back link and tab title (handed in by AppLayout: the routes
 *  import the pages, so the pages cannot import the routes). */
export const ModuleRoutesContext = createContext<RouteDef[]>([])

export const useModuleRoutes = () => useContext(ModuleRoutesContext)
