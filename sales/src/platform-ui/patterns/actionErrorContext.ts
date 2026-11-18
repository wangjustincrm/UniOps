import { createContext, useContext } from 'react'

// Where a refused action is also said beside where it was asked (2026-11-18): a dialog, a panel (ActionErrors).
export interface ActionErrorSlot { report: (error: unknown, messages?: Record<string, string>) => void; clear: () => void }
export const ActionErrorContext = createContext<ActionErrorSlot | null>(null)
export const useActionErrorSlot = () => useContext(ActionErrorContext)
