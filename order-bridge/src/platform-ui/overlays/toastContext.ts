import { createContext, useContext } from 'react'

export type ToastVariant = 'info' | 'success' | 'error'

export interface ToastInput {
  title: string
  description?: string
  variant?: ToastVariant
}

export const ToastContext = createContext<((t: ToastInput) => void) | null>(null)

export function useToast(): (t: ToastInput) => void {
  const show = useContext(ToastContext)
  if (!show) throw new Error('useToast must be used inside <ToastProvider>')
  return show
}
