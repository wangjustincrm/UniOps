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

export function DropdownPortal({ anchorRect, onClose, children, className, align = 'start', width, flipHeight }: {
  anchorRect: DOMRect
  onClose: () => void
  children: React.ReactNode
  className?: string
  /** end: right edges line up (a menu under a right-hand button). */
  align?: 'start' | 'end'
  /** Fixed width in px; default = the anchor's width. */
  width?: number
  /** Expected panel height in px. When set, the panel opens upward if that much
      does not fit below the anchor but does fit above it. */
  flipHeight?: number
}) {
  const w = width ?? anchorRect.width
  const rawLeft = align === 'end' ? anchorRect.right - w : anchorRect.left
  // Keep the panel inside the viewport (a wide panel under a narrow trigger near the edge).
  const left = Math.max(8, Math.min(rawLeft, window.innerWidth - w - 8))
  const up = flipHeight !== undefined
    && anchorRect.bottom + 4 + flipHeight > window.innerHeight && anchorRect.top > flipHeight
  const vertical = up ? { bottom: window.innerHeight - anchorRect.top + 4 } : { top: anchorRect.bottom + 4 }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  return createPortal(
    <>
      {/* Above Modal/Drawer (z-50): a dropdown opened inside a dialog must catch the
          outside click. DOM order cannot be relied on (a child portal mounting with
          its dialog is inserted before it), so the layer is explicitly higher. */}
      <div className="fixed inset-0 z-[60]" data-testid="dropdown-dismiss" onClick={onClose} />
      <div
        style={{ position: 'fixed', ...vertical, left, width: w }}
        className={cn('z-[60] max-h-64 overflow-y-auto rounded-lg border border-line bg-surface py-1 shadow-md', className)}
      >
        {children}
      </div>
    </>,
    document.body,
  )
}

/** Right-click menu at the pointer. Closes on outside click, right-click and Escape. */
export function ContextMenu({ x, y, onClose, children, className }: {
  x: number
  y: number
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
    <div className="fixed inset-0 z-[60]" onClick={onClose} onContextMenu={(e) => { e.preventDefault(); onClose() }}>
      <div
        role="menu"
        style={{ position: 'fixed', top: y, left: x }}
        className={cn('min-w-52 rounded-lg border border-line bg-surface py-1 shadow-md', className)}
        // Clicks inside the menu do not reach the closing layer (as the EPMS
        // original): an item closes the menu itself when it is done.
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>,
    document.body,
  )
}
