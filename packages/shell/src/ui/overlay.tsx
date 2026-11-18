/**
 * Modal, Drawer and ConfirmDialog. The only overlays in UniOps: pages never
 * draw their own `fixed inset-0` backdrop.
 *
 * All of them: render in a portal (so no parent overflow/transform clips them),
 * close on Escape and on the backdrop unless `busy`, move focus inside on open
 * and give it back on close, and keep Tab inside the panel.
 */
import React, { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, X } from 'lucide-react'
import { cn } from '../lib/cn'
import { Button } from './button'

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

function useOverlay(open: boolean, onClose: () => void, panel: React.RefObject<HTMLElement | null>, closeOnEscape = true) {
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    if (!open) return
    const before = document.activeElement as HTMLElement | null
    const el = panel.current
    // Focus the first autoFocus element, else the first focusable, else the panel.
    const target = el?.querySelector<HTMLElement>('[autofocus],[data-autofocus]') ?? el?.querySelector<HTMLElement>(FOCUSABLE) ?? el
    target?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && closeOnEscape) { e.stopPropagation(); close.current() }
      if (e.key === 'Tab' && el) {
        const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE))
        if (items.length === 0) return
        const first = items[0], last = items[items.length - 1]
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
      before?.focus?.()
    }
  }, [open, panel, closeOnEscape])
}

const modalWidth = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl', xl: 'max-w-4xl', full: 'max-w-[min(96vw,1400px)]' } as const

export interface ModalProps {
  open: boolean
  onClose: () => void
  title: React.ReactNode
  /** Line under the title, e.g. the document number it acts on. */
  description?: React.ReactNode
  /** Leading icon in the header (lucide element). */
  icon?: React.ReactNode
  size?: keyof typeof modalWidth
  /** Buttons, right-aligned in the footer. Primary action last. */
  footer?: React.ReactNode
  /** While true, Escape / backdrop / close button do nothing. */
  busy?: boolean
  /** Backdrop click closes. Default false for Modal (a stray click must not
   *  throw away what was typed), true for Drawer. */
  closeOnBackdrop?: boolean
  /** Escape closes (default true). Off for forms that only close by a button. */
  closeOnEscape?: boolean
  /** false: no close button, Escape and backdrop do nothing (a step the user
   *  must finish, e.g. a forced password change). */
  dismissible?: boolean
  className?: string
  bodyClassName?: string
  children?: React.ReactNode
}

export function Modal({
  open, onClose, title, description, icon, size = 'md', footer, busy, closeOnBackdrop = false, closeOnEscape = true,
  dismissible = true, className, bodyClassName, children,
}: ModalProps) {
  const panel = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const safeClose = useCallback(() => { if (!busy && dismissible) onClose() }, [busy, dismissible, onClose])
  useOverlay(open, safeClose, panel, closeOnEscape)
  if (!open) return null
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-inverse/40 p-4 animate-[overlay-in_150ms_ease-out]"
      onMouseDown={(e) => { if (closeOnBackdrop && e.target === e.currentTarget) safeClose() }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cn(
          'flex max-h-[calc(100vh-2rem)] w-full flex-col rounded-xl bg-surface shadow-lg outline-none animate-[modal-in_180ms_var(--ease-standard)]',
          modalWidth[size], className,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line-subtle px-5 py-4">
          <div className="flex min-w-0 items-start gap-3">
            {icon && <span className="mt-0.5 shrink-0 text-fg-muted [&>svg]:h-5 [&>svg]:w-5">{icon}</span>}
            <div className="min-w-0">
              <h2 id={titleId} className="text-base font-semibold text-fg">{title}</h2>
              {description && <div className="mt-0.5 text-sm text-fg-muted">{description}</div>}
            </div>
          </div>
          {dismissible && (
            <button
              type="button"
              onClick={safeClose}
              disabled={busy}
              aria-label="Close"
              className="-mr-1 rounded-md p-1.5 text-fg-faint transition-colors hover:bg-surface-sunken hover:text-fg-secondary disabled:opacity-40"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
        <div className={cn('min-h-0 flex-1 overflow-y-auto px-5 py-4', bodyClassName)}>{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 border-t border-line-subtle px-5 py-3">{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}

export interface DrawerProps extends Omit<ModalProps, 'size'> {
  side?: 'right' | 'left'
  width?: 'sm' | 'md' | 'lg' | 'xl'
}

const drawerWidth = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl', xl: 'max-w-4xl' } as const

/** Side panel for detail / edit without leaving the list. */
export function Drawer({
  open, onClose, title, description, icon, side = 'right', width = 'md', footer, busy, closeOnBackdrop = true,
  closeOnEscape = true, className, bodyClassName, children,
}: DrawerProps) {
  const panel = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const safeClose = useCallback(() => { if (!busy) onClose() }, [busy, onClose])
  useOverlay(open, safeClose, panel, closeOnEscape)
  if (!open) return null
  return createPortal(
    <div
      className={cn('fixed inset-0 z-50 flex bg-inverse/30 animate-[overlay-in_150ms_ease-out]', side === 'right' ? 'justify-end' : 'justify-start')}
      onMouseDown={(e) => { if (closeOnBackdrop && e.target === e.currentTarget) safeClose() }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cn('flex h-full w-full flex-col bg-surface shadow-lg outline-none', drawerWidth[width], className)}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line-subtle px-5 py-4">
          <div className="flex min-w-0 items-start gap-3">
            {icon && <span className="mt-0.5 shrink-0 text-fg-muted [&>svg]:h-5 [&>svg]:w-5">{icon}</span>}
            <div className="min-w-0">
              <h2 id={titleId} className="text-base font-semibold text-fg">{title}</h2>
              {description && <div className="mt-0.5 text-sm text-fg-muted">{description}</div>}
            </div>
          </div>
          <button type="button" onClick={safeClose} disabled={busy} aria-label="Close"
            className="-mr-1 rounded-md p-1.5 text-fg-faint transition-colors hover:bg-surface-sunken hover:text-fg-secondary disabled:opacity-40">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className={cn('min-h-0 flex-1 overflow-y-auto px-5 py-4', bodyClassName)}>{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 border-t border-line-subtle px-5 py-3">{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}

export interface ConfirmDialogProps {
  open?: boolean
  onClose: () => void
  title: string
  subtitle?: React.ReactNode
  message: React.ReactNode
  confirmLabel: string
  cancelLabel?: string
  /** danger for destructive actions (delete / cancel / void), primary otherwise. */
  tone?: 'danger' | 'primary'
  /** While a returned promise is pending the buttons are disabled; if it
   *  throws, its message shows in the dialog and the dialog stays open. */
  onConfirm: () => unknown
  failureFallback?: string
  /** Extra content between message and buttons (e.g. a reason field). */
  children?: React.ReactNode
  confirmDisabled?: boolean
  /** App-specific error → text (e.g. FastAPI validation arrays). */
  formatError?: (e: unknown, fallback: string) => string
}

function errorText(e: unknown, fallback: string): string {
  if (e && typeof e === 'object') {
    const anyE = e as { response?: { data?: { detail?: unknown } }; message?: unknown }
    const detail = anyE.response?.data?.detail
    if (typeof detail === 'string' && detail) return detail
    if (typeof anyE.message === 'string' && anyE.message) return anyE.message
  }
  return typeof e === 'string' && e ? e : fallback
}

export function ConfirmDialog({
  open = true, onClose, title, subtitle, message, confirmLabel, cancelLabel = 'Cancel', tone = 'danger',
  onConfirm, failureFallback = 'Something went wrong. Please try again.', children, confirmDisabled, formatError = errorText,
}: ConfirmDialogProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      await onConfirm()
      setBusy(false)
      onClose()
    } catch (e) {
      setBusy(false)
      setError(formatError(e, failureFallback))
    }
  }
  const danger = tone === 'danger'
  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={busy}
      size="sm"
      title={title}
      description={subtitle}
      icon={<AlertTriangle className={danger ? 'text-danger-fg' : 'text-warning-fg'} />}
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>{cancelLabel}</Button>
          <Button variant={danger ? 'danger' : 'primary'} size="sm" onClick={confirm} loading={busy} disabled={confirmDisabled} data-autofocus>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="whitespace-pre-line text-sm text-fg-secondary">{message}</div>
        {children}
        {error && (
          <p role="alert" className="whitespace-pre-line rounded-lg border border-danger-line bg-danger-soft px-3 py-2 text-xs text-danger-fg">{error}</p>
        )}
      </div>
    </Modal>
  )
}
