import { createContext } from 'react'
import type { HelpCatalog, HelpSource } from './types'

export interface HelpContextValue { source: HelpSource; onMissing?: (id: string) => void; catalog?: HelpCatalog }
export const HelpContext = createContext<HelpContextValue | null>(null)
