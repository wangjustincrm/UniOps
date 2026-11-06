import { confirmDiscard } from './unsaved'
import * as RD from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { KeyboardEvent, ReactNode } from 'react'
import { cn } from '../lib/cn'
import { SHEET_SIZES, type SheetSize } from './sheetSizes'

export type { SheetSize } from './sheetSizes'

export interface SheetProps {
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
  onKeyDown?: (e: KeyboardEvent<HTMLDivElement>) => void
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

/** Side panel for forms and details; a Radix dialog anchored right, in a portal. */
export function Sheet({
  open, onOpenChange, title, description, children, footer, className, size = 'md', header, bodyClassName, onKeyDown, statusBar,
}: SheetProps) {
  return (
    <RD.Root open={open} onOpenChange={(next) => { if (next || confirmDiscard()) onOpenChange?.(next) }}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-50 bg-black/30" />
        <RD.Content
          onKeyDown={onKeyDown}
          className={cn(
            'fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-neutral-200 bg-white shadow-2xl focus:outline-none',
            SHEET_SIZES[size],
            className,
          )}
        >
          {header ?? (
            <div className="flex items-start justify-between gap-4 border-b border-neutral-100 px-6 py-4">
              <div>
                <SheetTitle>{title}</SheetTitle>
                {description && <RD.Description className="mt-1 text-sm text-neutral-500">{description}</RD.Description>}
              </div>
              <SheetClose />
            </div>
          )}
          {(header || !description) && <RD.Description className="sr-only">{description ?? title}</RD.Description>}
          <div className={bodyClassName ?? 'flex-1 overflow-y-auto px-6 py-4'}>{children}</div>
          {statusBar}
          {footer && <div className="flex justify-end gap-2 border-t border-neutral-100 px-6 py-4">{footer}</div>}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  )
}
