/**
 * Anchored dropdown in a portal. Any dropdown/popover must render into
 * document.body with position: fixed, or the overflow container a form sits
 * in clips it (a project-wide rule). Moved here from EPMS
 * (components/ui/DropdownPortal) unchanged, plus Escape to close.
 */
import React, { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '../lib/cn'

/** Viewport rect of the anchor while `open`, kept current on scroll/resize. */
export function useAnchorRect<T extends HTMLElement>(open: boolean, anchorRef: React.RefObject<T | null>) {
  const [rect, setRect] = useState<DOMRect | null>(null)
  useEffect(() => {
    if (!open) { setRect(null); return }
    const update = () => setRect(anchorRef.current?.getBoundingClientRect() ?? null)
    update()
    window.addEventListener('scroll', update, true)
    window.addEventListener('resize', update)
    return () => {
      window.removeEventListener('scroll', update, true)
      window.removeEventListener('resize', update)
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  return rect
}

export function DropdownPortal({ anchorRect, onClose, children, className }: {
  anchorRect: DOMRect
  onClose: () => void
  children: React.ReactNode
  className?: string
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  return createPortal(
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        style={{ position: 'fixed', top: anchorRect.bottom + 4, left: anchorRect.left, width: anchorRect.width }}
        className={cn('z-50 max-h-64 overflow-y-auto rounded-lg border border-line bg-surface py-1 shadow-md', className)}
      >
        {children}
      </div>
    </>,
    document.body,
  )
}
