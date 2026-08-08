// Toast state hook — split from components/Toast.tsx (which renders the
// stack) so that file only exports a component, per the repo's
// react-refresh/only-export-components lint rule.
import { useCallback, useRef, useState } from 'react'

export type ToastKind = 'success' | 'error'

export interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

const AUTO_DISMISS_MS = 6000

export function useToasts() {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const push = useCallback((kind: ToastKind, message: string) => {
    const id = nextId.current++
    setToasts((prev) => [...prev, { id, kind, message }])
    if (kind === 'success') {
      window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
    }
    return id
  }, [dismiss])

  return {
    toasts,
    dismiss,
    success: (message: string) => push('success', message),
    error: (message: string) => push('error', message),
  }
}
