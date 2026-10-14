// Toast state hook — split from components/Toast.tsx (which renders the
// stack) so that file only exports a component, per the repo's
// react-refresh/only-export-components lint rule.
import { useCallback, useRef, useState } from 'react'

export type ToastKind = 'success' | 'error'

/** An optional single action button on a toast (e.g. WeekDrawer's
 *  "Recalculate now" — design §5.1: marking a maintenance week does not
 *  itself re-plan anything, so the toast offers the follow-up action
 *  instead of the drawer silently rearranging a planner's schedule). Kept
 *  to one action, not a list — no caller needs more than one today, and a
 *  toast is not the place for a button bar. */
export interface ToastAction {
  label: string
  onClick: () => void
}

export interface ToastItem {
  id: number
  kind: ToastKind
  message: string
  action?: ToastAction
}

const AUTO_DISMISS_MS = 6000

export function useToasts() {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const push = useCallback((kind: ToastKind, message: string, action?: ToastAction) => {
    const id = nextId.current++
    setToasts((prev) => [...prev, { id, kind, message, action }])
    // A toast carrying an action stays until the planner deals with it (or
    // dismisses it by hand) — auto-dismissing it on the same 6s timer as a
    // plain success toast risks the "Recalculate now" button vanishing
    // before it's even noticed.
    if (kind === 'success' && !action) {
      window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
    }
    return id
  }, [dismiss])

  return {
    toasts,
    dismiss,
    success: (message: string, action?: ToastAction) => push('success', message, action),
    error: (message: string) => push('error', message),
  }
}
