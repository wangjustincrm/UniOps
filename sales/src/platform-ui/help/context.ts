import { createContext } from 'react'
import type { HelpSource } from './types'

export interface HelpContextValue { source: HelpSource; onMissing?: (id: string) => void }
export const HelpContext = createContext<HelpContextValue | null>(null)
