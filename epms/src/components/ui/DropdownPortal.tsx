import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

// ─── Anchored search dropdown ───────────────────────────────────────────────
//
// Any dropdown/popover must createPortal into document.body with position:
// fixed, or it gets clipped by whatever overflow container the form happens to
// sit in — a project-wide rule, and one this codebase has already been bitten
// by. It lived as a private copy inside BOTH AgreementCreatePage and
// AgreementEditPage; Task 14 needed a third (the receipt vendor picker), which
// is where two copies stops being a coincidence. Both copies were verbatim
// identical, so this file is that shared body with nothing changed — the two
// pages render exactly as they did before the move.
//
// Deliberately NOT modelled on the invoice upload modal's vendor dropdown
// (InvoiceListPage), which is an `absolute`-positioned sibling with no portal:
// that one works only because its own container happens not to clip it today.

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

export function DropdownPortal({ anchorRect, onClose, children }: { anchorRect: DOMRect; onClose: () => void; children: React.ReactNode }) {
  return createPortal(
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        style={{ position: 'fixed', top: anchorRect.bottom + 4, left: anchorRect.left, width: anchorRect.width }}
        className="z-50 max-h-64 overflow-y-auto rounded-lg border border-neutral-200 bg-white py-1 shadow-lg"
      >
        {children}
      </div>
    </>,
    document.body
  )
}
