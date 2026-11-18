import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { ActionErrorContext } from './actionErrorContext'
import { ErrorMessage } from './ErrorMessage'

// Where a refused action is also said beside where it was asked (2026-11-18): a dialog, a panel. Any ActionButton
// inside reports its failure here (useAction), as well as in the status row; the next try clears it.

/** Shows the last failure of an action inside it, under its content. FormDialog has one. */
export function ActionErrors({ children }: { children: ReactNode }) {
  const [failed, setFailed] = useState<{ error: unknown; messages?: Record<string, string> } | null>(null)
  const report = useCallback((error: unknown, messages?: Record<string, string>) => setFailed({ error, messages }), [])
  const clear = useCallback(() => setFailed(null), [])
  const slot = useMemo(() => ({ report, clear }), [report, clear])
  return (
    <ActionErrorContext.Provider value={slot}>
      {children}
      {failed && <ErrorMessage error={failed.error} messages={failed.messages} />}
    </ActionErrorContext.Provider>
  )
}
