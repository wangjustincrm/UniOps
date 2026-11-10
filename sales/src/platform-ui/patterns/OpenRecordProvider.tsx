import type { ReactNode } from 'react'
import { OpenRecordNavigate, type Go } from './openRecordContext'

/** Hands the app's router navigate to openRecord / openView / goTo, so platform-ui needs no router. */
export function OpenRecordProvider({ navigate, children }: { navigate: Go; children?: ReactNode }) {
  return <OpenRecordNavigate.Provider value={navigate}>{children}</OpenRecordNavigate.Provider>
}
