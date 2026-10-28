import * as RT from '@radix-ui/react-toast'
import { useCallback, useState, type ReactNode } from 'react'
import { cn } from '../lib/cn'
import { ToastContext, type ToastInput, type ToastVariant } from './toastContext'

interface ToastItem extends ToastInput {
  id: number
}

const STYLE: Record<ToastVariant, string> = {
  info: 'border-neutral-200',
  success: 'border-success-600',
  error: 'border-danger-600',
}

let nextId = 1

export function ToastProvider({ children, duration = 5000 }: { children: ReactNode; duration?: number }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const show = useCallback((t: ToastInput) => setItems((xs) => [...xs, { ...t, id: nextId++ }]), [])
  const remove = (id: number) => setItems((xs) => xs.filter((x) => x.id !== id))

  return (
    <ToastContext.Provider value={show}>
      <RT.Provider duration={duration}>
        {children}
        {items.map((t) => (
          <RT.Root
            key={t.id}
            onOpenChange={(open) => { if (!open) remove(t.id) }}
            className={cn('rounded-lg border-l-4 bg-white p-4 shadow-lg', STYLE[t.variant ?? 'info'])}
          >
            <RT.Title className="text-sm font-semibold text-neutral-900">{t.title}</RT.Title>
            {t.description && <RT.Description className="mt-1 text-sm text-neutral-600">{t.description}</RT.Description>}
          </RT.Root>
        ))}
        <RT.Viewport className="fixed bottom-4 right-4 z-[60] flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2 outline-none" />
      </RT.Provider>
    </ToastContext.Provider>
  )
}
