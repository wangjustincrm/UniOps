import { createContext } from 'react'

/** The app's router navigate, for openRecord / openView / goTo (see openRecord.ts). */
export type Go = (to: string) => void
export const OpenRecordNavigate = createContext<Go | null>(null)
