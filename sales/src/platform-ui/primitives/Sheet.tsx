import { UnsavedBoundary, useUnsavedScope, whenDiscarded, type UnsavedScope } from './unsaved'
import * as RD from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useContext, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import { cn } from '../lib/cn'
import { useReturnFocus } from './focusReturn'
import { SHEET_SIZES, type SheetSize } from './sheetSizes'
import { SheetLayerContext } from './sheetLayer'
import { BREAKPOINTS, useMediaQuery } from './useResponsive'

const PHONE = `(max-width: ${BREAKPOINTS.sm - 0.02}px)`
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

export type { SheetSize } from './sheetSizes'

export interface SheetProps {
  /** The scope its forms report to (a RecordSheet nests its tab's inside); default: one of its own. */
  unsaved?: UnsavedScope
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children?: ReactNode
  footer?: ReactNode
  className?: string
  size?: SheetSize
  /** Replaces the default header (title + close); it must render a SheetTitle. */
  header?: ReactNode
  /** A row pinned under the body, outside its scroll (a record sheet's status row). */
  statusBar?: ReactNode
  /** The body's own classes (default: padded and scrolling). */
  bodyClassName?: string
  onKeyDown?: (e: ReactKeyboardEvent<HTMLDivElement>) => void
}

/** The sheet's accessible title (inside a custom `header`). */
export const SheetTitle = ({ children, className }: { children: ReactNode; className?: string }) => (
  <RD.Title className={cn('text-base font-semibold text-neutral-900', className)}>{children}</RD.Title>
)

/** Closes the sheet (inside a custom `header`). */
export const SheetClose = ({ children, label = 'Close' }: { children?: ReactNode; label?: string }) => (
  <RD.Close className="inline-flex h-7 w-7 items-center justify-center rounded-md text-neutral-500 hover:bg-neutral-100 hover:text-neutral-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600"
    aria-label={label}>
    {children ?? <X className="h-4 w-4" />}
  </RD.Close>
)

/** Tab stays inside the sheet (it is not modal in its tab: the tab bar stays usable with the mouse). */
function keepTabInside(e: ReactKeyboardEvent<HTMLDivElement>) {
  if (e.key !== 'Tab') return
  const all = [...e.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement)
  if (!all.length) return
  const first = all[0], last = all[all.length - 1]
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
}

/** Side panel for forms and details; a Radix dialog anchored right. In an app tab (SheetLayerContext) it is drawn in
 *  that tab's layer: under the tab bar, never over it, hidden with its tab and back as it was (user 2026-11-27); the
 *  tab bar stays usable while it is open. Elsewhere, and on a phone, over the window (modal), as before. */
export function Sheet({
  open, onOpenChange, title, description, children, footer, className, size = 'md', header, bodyClassName, onKeyDown, statusBar,
  unsaved: given,
}: SheetProps) {
  const own = useUnsavedScope()  // closing asks only about the forms in this sheet
  const unsaved = given ?? own
  const returnFocus = useReturnFocus()
  const layer = useContext(SheetLayerContext)
  const phone = useMediaQuery(PHONE)
  const inTab = !!layer && !phone
  const change = (next: boolean) => { if (next) onOpenChange?.(true); else whenDiscarded(unsaved, () => onOpenChange?.(false)) }
  return (
    <RD.Root open={open} modal={!inTab} onOpenChange={change}>
      <RD.Portal container={inTab ? layer : undefined}>
        {inTab
          // Over its own tab's page only; a click on it closes the sheet as the window's overlay does.
          ? open && <div aria-hidden data-sheet-overlay className="pointer-events-auto absolute inset-0 bg-black/30" onClick={() => change(false)} />
          : <RD.Overlay className="fixed inset-0 z-50 bg-black/30" />}
        <RD.Content
          onKeyDown={(e) => { if (inTab) keepTabInside(e); onKeyDown?.(e) }}
          {...returnFocus}
          // In a tab: the tab bar, another tab, a menu or a dialog opened from the sheet is not "outside" it.
          {...(inTab ? {
            onInteractOutside: (e: Event) => e.preventDefault(),
            // Esc in another tab never closes this one's hidden sheet.
            onEscapeKeyDown: (e: KeyboardEvent) => { if (layer!.closest('[hidden]')) e.preventDefault() },
          } : {})}
          className={cn(
            inTab ? 'pointer-events-auto absolute inset-y-0 right-0 flex w-full flex-col border-l border-neutral-200 bg-white shadow-2xl focus:outline-none'
              : 'fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-neutral-200 bg-white shadow-2xl focus:outline-none',
            SHEET_SIZES[size],
            className,
          )}
        >
          <UnsavedBoundary scope={unsaved}>
          {header ?? (
            <div className="flex items-start justify-between gap-4 border-b border-neutral-100 px-4 py-4 sm:px-6">
              <div>
                <SheetTitle>{title}</SheetTitle>
                {description && <RD.Description className="mt-1 text-sm text-neutral-500">{description}</RD.Description>}
              </div>
              <SheetClose />
            </div>
          )}
          {(header || !description) && <RD.Description className="sr-only">{description ?? title}</RD.Description>}
          <div className={bodyClassName ?? 'flex-1 overflow-y-auto px-4 py-4 sm:px-6'}>{children}</div>
          {statusBar}
          {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-neutral-100 px-4 py-4 sm:px-6">{footer}</div>}
          </UnsavedBoundary>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  )
}
