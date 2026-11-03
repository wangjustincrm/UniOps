import type { ReactNode } from 'react'
import { IconPopover } from '../primitives/IconPopover'
import { cn } from '../lib/cn'

/** A toolbar control that is an icon (aria-label, tooltip, optional count)
 *  opening a small popover with a title, a body and its actions — Save as new,
 *  the advanced Search. The one way to add such a control to a toolbar. */
export function ToolbarPopover({ label, icon, badge, title, open, onOpenChange, children, actions, width = 'w-80', disabled }: {
  label: string; icon: ReactNode; badge?: number; title: string; open: boolean; onOpenChange: (open: boolean) => void
  children: ReactNode; actions?: ReactNode; width?: string; disabled?: boolean
}) {
  return (
    <IconPopover label={label} icon={icon} badge={badge} open={open} onOpenChange={onOpenChange} disabled={disabled}
      className={cn('flex max-h-[75vh] max-w-[calc(100vw-2rem)] flex-col gap-3 overflow-y-auto', width)}>
      <h3 className="text-sm font-semibold text-neutral-800">{title}</h3>
      <div className="flex flex-col gap-2">{children}</div>
      {actions && <div className="flex justify-end gap-2 border-t border-neutral-100 pt-2">{actions}</div>}
    </IconPopover>
  )
}
