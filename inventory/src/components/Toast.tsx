// Toast stack renderer — no toast library exists anywhere in this repo
// (checked oa/epms/vms/booking), so this is a small self-contained
// implementation. Renders via createPortal to document.body (repo
// convention for anything that must escape an overflow/clipping container
// — see feedback_uniops_overlay_dropdown_portal). State lives in
// hooks/useToasts.ts (split out so this file only exports a component, per
// react-refresh/only-export-components). Every mutation in ForecastPage
// routes success/error through this so nothing succeeds or fails silently
// (design spec §6.6: "所有提交动作走 loading -> success/error 三态，成功给
// toast，禁止静默成功").
import { createPortal } from 'react-dom'
import { CheckCircle2, AlertCircle, X as XIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ToastItem } from '@/hooks/useToasts'

export function ToastStack({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: number) => void }) {
  if (toasts.length === 0) return null
  return createPortal(
    <div className="fixed bottom-4 right-4 z-[100] flex w-full max-w-sm flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          role="alert"
          className={cn(
            'flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm shadow-lg',
            t.kind === 'success'
              ? 'border-success-200 bg-success-50 text-success-800'
              : 'border-danger-200 bg-danger-50 text-danger-800',
          )}
        >
          {t.kind === 'success'
            ? <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" />
            : <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />}
          <span className="flex-1">{t.message}</span>
          {t.action && (
            <button
              type="button"
              onClick={() => { t.action!.onClick(); onDismiss(t.id) }}
              className="shrink-0 rounded-md border border-current/30 px-2 py-1 text-xs font-medium hover:bg-current/10"
            >
              {t.action.label}
            </button>
          )}
          <button
            type="button"
            onClick={() => onDismiss(t.id)}
            aria-label="Dismiss notification"
            className="shrink-0 text-current/60 hover:text-current"
          >
            <XIcon className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>,
    document.body,
  )
}
