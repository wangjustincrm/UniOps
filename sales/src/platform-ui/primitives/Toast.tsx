import { useCallback, type ReactNode } from 'react'
import { Toaster, toast } from 'sonner'
import { ToastContext, type ToastInput } from './toastContext'

/** Floating toasts (shadcn's Sonner, which replaced its Toast; 2026-11-23). The API stays `useToast()(…)`: only this
 *  provider knows Sonner. A list's or a sheet's outcome is not a toast: it goes to its status row (patterns/notices). */
export function ToastProvider({ children, duration = 5000 }: { children: ReactNode; duration?: number }) {
  const show = useCallback((t: ToastInput) => {
    const opts = { description: t.description, duration }
    if (t.variant === 'success') toast.success(t.title, opts)
    else if (t.variant === 'error') toast.error(t.title, opts)
    else toast(t.title, opts)
  }, [duration])
  return (
    <ToastContext.Provider value={show}>
      {children}
      <Toaster position="bottom-right" richColors closeButton toastOptions={{ className: 'text-sm' }} />
    </ToastContext.Provider>
  )
}
